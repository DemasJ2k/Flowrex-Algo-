"""
AlgoEngine (singleton) — manages multiple AgentRunner asyncio tasks.
AgentRunner (per-agent) — polling loop that evaluates signals and executes trades.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.websocket import get_ws_manager
from app.models.agent import TradingAgent, AgentLog, AgentTrade
from app.services.broker.manager import get_broker_manager
from app.services.agent.flowrex_agent import FlowrexAgent


POLL_INTERVAL = 60  # seconds (was 30 — increased to reduce Oanda rate limiting with 5 agents)


class AgentRunner:
    """Per-agent polling loop."""

    def __init__(self, agent_id: int):
        self.agent_id = agent_id
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._paused = False
        self._agent: Optional[FlowrexAgent] = None
        self._active_direction: Optional[str] = None
        self._bar_buffer: list[dict] = []
        self._last_bar_time: int = 0
        self._warmup_done = False
        self._eval_count = 0
        self._signal_count = 0
        self._daily_pnl = 0.0
        self._daily_trade_count = 0
        self._daily_reset_date: Optional[str] = None

    async def start(self):
        """Load agent config from DB and start the polling loop."""
        db = SessionLocal()
        try:
            agent_record = db.query(TradingAgent).filter(TradingAgent.id == self.agent_id).first()
            if not agent_record:
                self._log_to_db(db, "error", "Agent not found in DB")
                db.commit()
                return

            # Instantiate agent based on agent_type
            agent_type = agent_record.agent_type or "flowrex"
            agent_config = {
                **(agent_record.risk_config or {}),
                "timeframe": agent_record.timeframe or "M5",
            }
            agent_args = (self.agent_id, agent_record.symbol, agent_record.broker_name, agent_config)

            try:
                if agent_type == "potential":
                    from app.services.agent.potential_agent import PotentialAgent
                    self._agent = PotentialAgent(*agent_args)
                elif agent_type == "scalping":
                    from app.services.agent.scalping_agent import ScalpingAgent
                    self._agent = ScalpingAgent(*agent_args)
                elif agent_type == "expert":
                    from app.services.agent.expert_agent import ExpertAgent
                    self._agent = ExpertAgent(*agent_args)
                else:
                    self._agent = FlowrexAgent(*agent_args)
            except Exception as e:
                self._log_to_db(db, "error", f"Failed to create agent: {e}")
                db.commit()
                return

            # Inject logging callback
            self._agent._log_fn = lambda level, msg, data=None: self._log(level, msg, data)

            # Load ML models
            try:
                if not self._agent.load():
                    self._log_to_db(db, "warn", "No ML models loaded — agent will not produce signals")
            except Exception as e:
                self._log_to_db(db, "error", f"Failed to load models: {e}")
                db.commit()
                return

            self._running = True
            self._log_to_db(db, "info", f"Agent started — polling {agent_record.broker_name} every {POLL_INTERVAL}s for {agent_record.symbol}/M5")
            db.commit()
        except Exception as e:
            self._log_to_db(db, "error", f"Start failed: {e}")
            db.commit()
        finally:
            db.close()

        # Start the loop as an asyncio task
        if self._running:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """Stop the polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        db = SessionLocal()
        try:
            self._log_to_db(db, "info", f"Agent stopped — {self._eval_count} evals, {self._signal_count} signals")
            db.commit()
        finally:
            db.close()

    async def pause(self):
        """Pause evaluation (loop continues but skips evaluate)."""
        self._paused = True
        db = SessionLocal()
        try:
            self._log_to_db(db, "info", "Agent paused")
            db.commit()
        finally:
            db.close()

    async def resume(self):
        """Resume evaluation."""
        self._paused = False

    async def _run_loop(self):
        """Main polling loop — runs every POLL_INTERVAL seconds."""
        while self._running:
            try:
                if not self._paused:
                    await self._poll_and_evaluate()
            except asyncio.CancelledError:
                break
            except Exception as e:
                db = SessionLocal()
                try:
                    self._log_to_db(db, "error", f"Loop error: {e}")
                    db.commit()
                finally:
                    db.close()

            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_and_evaluate(self):
        """Fetch candles, detect new bar, evaluate signal."""
        if not self._agent:
            return

        db = SessionLocal()
        try:
            agent_record = db.query(TradingAgent).filter(TradingAgent.id == self.agent_id).first()
            if not agent_record or agent_record.status != "running":
                return

            # Get broker adapter (always needed for trade execution)
            manager = get_broker_manager()
            adapter = manager.get_adapter(agent_record.created_by, agent_record.broker_name)
            if not adapter:
                self._log_to_db(db, "warn", f"No broker connected ({agent_record.broker_name})")
                db.commit()
                return

            # Fetch M5 candles — prefer Databento for supported symbols
            candles = None
            data_source = "broker"

            # Check if user has Databento configured and symbol is supported
            from app.services.data.databento_adapter import SYMBOL_MAP as DB_SYMBOLS
            if agent_record.symbol in DB_SYMBOLS:
                try:
                    from app.models.market_data import MarketDataProvider
                    from app.core.encryption import get_fernet
                    provider = db.query(MarketDataProvider).filter(
                        MarketDataProvider.user_id == agent_record.created_by,
                        MarketDataProvider.provider_name == "databento",
                        MarketDataProvider.is_active == True,
                    ).first()
                    if provider:
                        from app.services.data.databento_adapter import DatabentoAdapter
                        api_key = get_fernet().decrypt(provider.api_key_encrypted.encode()).decode()
                        db_adapter = DatabentoAdapter(api_key)
                        db_candles = await db_adapter.get_candles(agent_record.symbol, "M5", 500)
                        await db_adapter.close()
                        if db_candles and len(db_candles) >= 100:
                            candles = db_candles
                            data_source = "databento"
                except Exception as e:
                    # Databento failed — fall back to broker silently
                    pass

            # Fall back to broker candles if Databento didn't work
            if candles is None:
                candles = await adapter.get_candles(agent_record.symbol, "M5", 500)
                data_source = "broker"

            if not candles:
                return

            # Convert to dicts
            if data_source == "databento":
                from dataclasses import asdict
                bars = [{"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles]
            else:
                bars = [{"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles]

            # Detect new closed bar
            last_time = bars[-1]["time"] if bars else 0
            if last_time <= self._last_bar_time:
                return  # No new bar

            self._last_bar_time = last_time
            self._bar_buffer = bars

            # Log data source on first bar (once)
            if not self._warmup_done:
                self._log_to_db(db, "info",
                    f"Data source: {data_source.upper()} | {len(bars)} bars loaded for {agent_record.symbol}/M5")

            # Warm-up: first fetch loads the bar buffer but does NOT evaluate.
            # This ensures we wait for the NEXT new bar close before trading.
            if not self._warmup_done:
                self._warmup_done = True
                self._log_to_db(db, "info",
                    f"Warm-up complete: loaded {len(bars)} bars, waiting for next bar close before evaluating")
                db.commit()
                return

            # Reset daily counters
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._daily_reset_date != today:
                self._daily_pnl = 0.0
                self._daily_trade_count = 0
                self._daily_reset_date = today

            # Get account balance
            try:
                account = await adapter.get_account_info()
                balance = account.balance
            except Exception:
                balance = 10000.0  # Fallback

            # Check for closed trades EVERY poll (not just on new bars)
            await self._check_closed_trades(adapter, agent_record, db)

            self._eval_count += 1

            # Evaluate signal
            signal = await self._agent.evaluate(
                m5_bars=self._bar_buffer,
                broker_adapter=adapter,
                balance=balance,
                daily_pnl=self._daily_pnl,
                daily_trade_count=self._daily_trade_count,
                current_bar_index=self._eval_count,
            )

            if signal:
                # Log signal evaluation
                self._log_to_db(db, "signal",
                    f"Eval #{self._eval_count}: SIGNAL {signal['direction']} {agent_record.symbol} "
                    f"conf={signal['confidence']:.3f}",
                    signal)

                # Portfolio-level check: limit total open positions
                engine = get_algo_engine()
                open_count = db.query(AgentTrade).filter(AgentTrade.status == "open").count()
                max_open = (agent_record.risk_config or {}).get("max_positions", 6)
                if open_count >= max_open:
                    self._log_to_db(db, "info", f"Portfolio limit: {open_count}/{max_open} positions open — skipping trade")
                else:
                    await self._create_trade(signal, adapter, agent_record, db)
            else:
                # Log rejection evaluation
                self._log_to_db(db, "eval",
                    f"Eval #{self._eval_count}: no signal | "
                    f"bars={len(self._bar_buffer)}, balance={balance:.2f}")

            # Check if any open trades have been closed by TP/SL
            await self._check_closed_trades(adapter, agent_record, db)

            # Health check every 12 evaluations (~1 hour on M5)
            if self._eval_count % 12 == 0:
                self._log_to_db(db, "info",
                    f"Health: {self._eval_count} evals, {self._signal_count} signals, "
                    f"bars={len(self._bar_buffer)}, direction={self._active_direction}"
                )

            db.commit()
        except Exception as e:
            self._log_to_db(db, "error", f"Evaluate error: {e}")
            db.commit()
        finally:
            db.close()

    async def _check_closed_trades(self, adapter, agent_record, db: Session):
        """Check if any open AgentTrade records have been closed by broker (TP/SL hit)."""
        try:
            open_trades = (
                db.query(AgentTrade)
                .filter(AgentTrade.agent_id == self.agent_id, AgentTrade.status == "open")
                .all()
            )
            if not open_trades:
                return

            # Get current broker positions
            broker_positions = await adapter.get_positions()
            broker_symbols = {p.symbol for p in broker_positions}

            for trade in open_trades:
                # Match by broker_ticket (unique trade ID) — NOT by symbol
                has_position = any(
                    str(p.id) == str(trade.broker_ticket)
                    for p in broker_positions
                )
                if not has_position:
                    # Trade was closed by broker (TP/SL hit or manual close)
                    now = datetime.now(timezone.utc)
                    # Determine exit reason based on last price vs SL/TP
                    exit_reason = "closed"
                    if trade.stop_loss and trade.take_profit:
                        # Use last bar close as approximate exit price
                        last_close = self._bar_buffer[-1]["close"] if self._bar_buffer else 0
                        if trade.direction == "BUY":
                            if last_close <= trade.stop_loss:
                                exit_reason = "SL_HIT"
                            elif last_close >= trade.take_profit:
                                exit_reason = "TP_HIT"
                        else:  # SELL
                            if last_close >= trade.stop_loss:
                                exit_reason = "SL_HIT"
                            elif last_close <= trade.take_profit:
                                exit_reason = "TP_HIT"

                    trade.status = "closed"
                    trade.exit_time = now
                    trade.exit_reason = exit_reason
                    # Approximate exit price from last bar
                    if self._bar_buffer:
                        trade.exit_price = self._bar_buffer[-1]["close"]

                    # Calculate P&L from entry/exit prices
                    if trade.exit_price and trade.entry_price:
                        price_diff = trade.exit_price - trade.entry_price
                        if trade.direction == "SELL":
                            price_diff = -price_diff
                        trade.pnl = round(price_diff * trade.lot_size, 2)

                    pnl_str = f"P&L:{trade.broker_pnl or trade.pnl or '?'}"
                    self._log_to_db(db, "trade",
                        f"CLOSED {trade.direction} {trade.symbol} | {exit_reason} | "
                        f"Entry:{trade.entry_price} Exit:{trade.exit_price} | {pnl_str} | "
                        f"Ticket:{trade.broker_ticket}",
                    )

                    # Update daily P&L
                    if trade.broker_pnl is not None:
                        self._daily_pnl += trade.broker_pnl
                    elif trade.pnl is not None:
                        self._daily_pnl += trade.pnl

                    # Reset active direction if this was our tracked position
                    if self._active_direction == trade.direction:
                        self._active_direction = None

        except Exception as e:
            self._log_to_db(db, "warn", f"Position check error: {e}")

    async def _create_trade(self, signal: dict, adapter, agent_record, db: Session):
        """Execute trade via broker and record in DB."""
        direction = signal["direction"]

        # Check active direction (no duplicate positions)
        if self._active_direction == direction:
            return

        # Set active direction BEFORE calling broker (avoid race condition)
        self._active_direction = direction
        self._signal_count += 1

        # Log signal details BEFORE placing the order
        reason = signal.get("reason", "unknown")
        self._log_to_db(db, "signal",
            f"PLACING {direction} {agent_record.symbol} | "
            f"Entry:{signal['entry_price']} SL:{signal['stop_loss']} TP:{signal['take_profit']} | "
            f"Conf:{signal['confidence']:.3f} | Size:{signal['lot_size']} | Model:{reason}",
            signal,
        )

        # Place order through broker
        try:
            result = await adapter.place_order(
                symbol=agent_record.symbol,
                side=direction,
                size=signal["lot_size"],
                order_type="MARKET",
                sl=signal["stop_loss"],
                tp=signal["take_profit"],
            )

            if result.success:
                # Only record trade in DB if broker confirmed the order
                trade = AgentTrade(
                    agent_id=self.agent_id,
                    symbol=agent_record.symbol,
                    direction=direction,
                    entry_price=signal["entry_price"],
                    stop_loss=signal["stop_loss"],
                    take_profit=signal["take_profit"],
                    lot_size=signal["lot_size"],
                    status="open",
                    confidence=signal["confidence"],
                    signal_data=signal,
                    entry_time=datetime.now(timezone.utc),
                    broker_ticket=result.order_id,
                )
                db.add(trade)
                self._daily_trade_count += 1

                self._log_to_db(db, "trade",
                    f"OPENED {direction} {agent_record.symbol} @ {signal['entry_price']} | "
                    f"SL:{signal['stop_loss']} TP:{signal['take_profit']} | "
                    f"Size:{signal['lot_size']} | Ticket:{result.order_id} | "
                    f"Conf:{signal['confidence']:.3f} | Model:{reason}",
                    signal,
                )
            else:
                self._log_to_db(db, "error",
                    f"ORDER FAILED {direction} {agent_record.symbol} | "
                    f"Broker error: {result.message}",
                    signal,
                )
                self._active_direction = None

        except Exception as e:
            self._log_to_db(db, "error",
                f"EXECUTION ERROR {direction} {agent_record.symbol} | {e}",
            )
            self._active_direction = None

    def _log(self, level: str, message: str, data: dict = None):
        """Log to DB (creates own session)."""
        db = SessionLocal()
        try:
            self._log_to_db(db, level, message, data)
            db.commit()
        finally:
            db.close()

    def _log_to_db(self, db: Session, level: str, message: str, data: dict = None):
        """Write log entry to agent_logs table AND broadcast via WebSocket."""
        log = AgentLog(
            agent_id=self.agent_id,
            level=level,
            message=message,
            data=data,
        )
        db.add(log)

        # Broadcast via WebSocket (fire-and-forget)
        try:
            ws_manager = get_ws_manager()
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(ws_manager.broadcast(
                    f"agent:{self.agent_id}",
                    {"type": "log", "data": {"level": level, "message": message, "data": data}},
                ))
        except Exception:
            pass  # WS broadcast is best-effort


class AlgoEngine:
    """
    Singleton engine managing multiple AgentRunner tasks.
    """

    def __init__(self):
        self._runners: dict[int, AgentRunner] = {}

    async def start_agent(self, agent_id: int):
        """Start an agent's polling loop."""
        if agent_id in self._runners:
            return  # Already running

        runner = AgentRunner(agent_id)
        self._runners[agent_id] = runner
        await runner.start()

    async def stop_agent(self, agent_id: int):
        """Stop an agent's polling loop."""
        runner = self._runners.pop(agent_id, None)
        if runner:
            await runner.stop()

    async def pause_agent(self, agent_id: int):
        """Pause an agent (loop continues but skips evaluation)."""
        runner = self._runners.get(agent_id)
        if runner:
            await runner.pause()

    async def resume_agent(self, agent_id: int):
        """Resume a paused agent."""
        runner = self._runners.get(agent_id)
        if runner:
            await runner.resume()

    def get_running_agents(self) -> list[int]:
        """Return list of running agent IDs."""
        return list(self._runners.keys())

    def is_running(self, agent_id: int) -> bool:
        return agent_id in self._runners

    async def stop_all(self):
        """Stop all running agents (e.g. on shutdown)."""
        for agent_id in list(self._runners.keys()):
            await self.stop_agent(agent_id)

    def reload_models_for_symbol(self, symbol: str):
        """Hot-reload models for all running agents trading a specific symbol."""
        reloaded = 0
        for runner in self._runners.values():
            if runner._agent and getattr(runner._agent, "symbol", None) == symbol:
                try:
                    # Reload all ensemble models from disk
                    if hasattr(runner._agent, "_ensemble_scalping"):
                        runner._agent._ensemble_scalping.load_models()
                        reloaded += 1
                    if hasattr(runner._agent, "_ensemble_expert"):
                        runner._agent._ensemble_expert.load_models()
                        reloaded += 1
                except Exception:
                    pass  # Non-fatal — agent continues with old model
        return reloaded


# Singleton
_engine: Optional[AlgoEngine] = None


def get_algo_engine() -> AlgoEngine:
    global _engine
    if _engine is None:
        _engine = AlgoEngine()
    return _engine
