"""
AlgoEngine (singleton) — manages multiple AgentRunner asyncio tasks.
AgentRunner (per-agent) — polling loop that evaluates signals and executes trades.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.websocket import get_ws_manager
from app.models.agent import TradingAgent, AgentLog, AgentTrade
from app.services.broker.manager import get_broker_manager
from app.services.agent.flowrex_agent import FlowrexAgent

logger = logging.getLogger("flowrex.engine")


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
        self._last_bar_hash: str = ""  # stale-data / duplicate-bar detection
        self._warmup_done = False
        self._eval_count = 0
        self._signal_count = 0
        self._daily_pnl = 0.0
        self._daily_trade_count = 0
        self._daily_reset_date: Optional[str] = None
        self._eval_lock = asyncio.Lock()
        self._last_reconciliation = 0.0  # wall time of last full broker reconciliation
        self._market_hours_cached: Optional[tuple[float, bool, str]] = None  # (expires_at, is_open, reason)

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
                elif agent_type == "flowrex_v2":
                    from app.services.agent.flowrex_agent_v2 import FlowrexAgentV2
                    self._agent = FlowrexAgentV2(*agent_args)
                elif agent_type in ("scalping", "expert"):
                    # Deprecated agent types — removed in Batch 11 (2026-04-16).
                    # Models were archived, test files deleted. If a user has a stale
                    # DB record with one of these types, refuse to start with a clear error.
                    self._log_to_db(db, "error",
                        f"Agent type '{agent_type}' is deprecated. "
                        f"Please create a new agent with type 'flowrex_v2' or 'potential'.")
                    db.commit()
                    return
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

            # Pre-flight: validate symbol exists on broker + feature count matches
            try:
                manager = get_broker_manager()
                adapter = manager.get_adapter(agent_record.created_by, agent_record.broker_name)
                if adapter:
                    # Symbol check — try fetching 1 candle; if zero rows, broker doesn't support it
                    try:
                        test_candles = await adapter.get_candles(agent_record.symbol, "M5", 1)
                        if not test_candles:
                            self._log_to_db(db, "error",
                                f"Symbol '{agent_record.symbol}' not available on {agent_record.broker_name}. "
                                f"Check symbol name matches broker's instrument list.")
                            db.commit()
                            return
                    except Exception as sym_err:
                        self._log_to_db(db, "warn",
                            f"Symbol pre-flight check failed (non-fatal): {sym_err}")

                # Feature count check — only if agent has EXPECTED_FEATURE_COUNT
                expected = getattr(self._agent, "EXPECTED_FEATURE_COUNT", None)
                if expected:
                    model_features = getattr(self._agent, "feature_names", [])
                    if len(model_features) != expected:
                        self._log_to_db(db, "error",
                            f"Feature count mismatch: models have {len(model_features)} features, "
                            f"pipeline expects {expected}. Agent will not start — models must be retrained.")
                        db.commit()
                        return
            except Exception as pre_err:
                self._log_to_db(db, "warn", f"Pre-flight check error (non-fatal): {pre_err}")

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
            # Proactive market-hours check — skip poll entirely if closed.
            # Cached for 5 min to avoid opening a DB session on every poll tick.
            try:
                import time as _wall
                now_w = _wall.time()
                cached = self._market_hours_cached
                if cached and now_w < cached[0]:
                    _open, _reason = cached[1], cached[2]
                    _symbol = None
                else:
                    from app.services.market_hours import is_market_open
                    _db = SessionLocal()
                    try:
                        _rec = _db.query(TradingAgent).filter(TradingAgent.id == self.agent_id).first()
                        if not _rec or _rec.status != "running":
                            _db.close()
                            await asyncio.sleep(POLL_INTERVAL)
                            continue
                        _symbol = _rec.symbol
                        _open, _reason = is_market_open(_symbol)
                        self._market_hours_cached = (now_w + 300, _open, _reason)
                    finally:
                        try: _db.close()
                        except Exception: pass

                if not _open:
                    from app.services.market_hours import seconds_until_open
                    # Need symbol for seconds_until_open — refetch if cache hit skipped it
                    if _symbol is None:
                        _db = SessionLocal()
                        try:
                            _rec = _db.query(TradingAgent).filter(TradingAgent.id == self.agent_id).first()
                            _symbol = _rec.symbol if _rec else None
                        finally:
                            _db.close()
                    wait_sec = min(3600, max(300, seconds_until_open(_symbol) if _symbol else 1800))
                    self._log("info", f"Market closed ({_reason}); sleeping {wait_sec // 60} min")
                    await asyncio.sleep(wait_sec)
                    self._market_hours_cached = None  # force re-check after sleep
                    continue
            except Exception:
                pass  # advisory — never block on errors

            try:
                if not self._paused:
                    await self._poll_and_evaluate()
                    self._consecutive_errors = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._consecutive_errors = getattr(self, "_consecutive_errors", 0) + 1
                err_str = str(e)
                # Market-closed: back off instead of logging an error every 60s all weekend
                if "MARKET_CLOSED" in err_str or "market" in err_str.lower() and "closed" in err_str.lower():
                    backoff = min(3600, 600 * self._consecutive_errors)  # 10min, 20min, ... max 1hr
                    self._log("info", f"Market closed — backing off {backoff}s")
                    await asyncio.sleep(backoff)
                    continue
                db = SessionLocal()
                try:
                    self._log_to_db(db, "error", f"Loop error: {e}")
                    # AI error diagnosis (rate-limited inside monitoring.on_error)
                    agent_record = db.query(TradingAgent).filter(TradingAgent.id == self.agent_id).first()
                    if agent_record:
                        try:
                            from app.services.llm.monitoring import on_error
                            agent_dict = {
                                "id": agent_record.id,
                                "symbol": agent_record.symbol,
                                "agent_type": agent_record.agent_type,
                                "name": agent_record.name,
                            }
                            asyncio.create_task(on_error(
                                agent_record.created_by, self.agent_id,
                                err_str, agent_dict, error_kind="loop"
                            ))
                        except Exception:
                            pass
                    db.commit()
                finally:
                    db.close()

                # Exponential backoff on repeated errors — cap at 5 min so we
                # still recover from transient broker outages promptly.
                if self._consecutive_errors > 1:
                    extra = min(300, 30 * self._consecutive_errors)
                    await asyncio.sleep(extra)

            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_and_evaluate(self):
        """Fetch candles, detect new bar, evaluate signal."""
        if not self._agent:
            return

        async with self._eval_lock:
            await self._poll_and_evaluate_inner()

    async def _poll_and_evaluate_inner(self):
        """Inner evaluation logic, called under _eval_lock."""
        db = SessionLocal()
        try:
            agent_record = db.query(TradingAgent).filter(TradingAgent.id == self.agent_id).first()
            if (not agent_record
                or agent_record.status != "running"
                or agent_record.deleted_at is not None):
                return

            # Get broker adapter (always needed for trade execution)
            manager = get_broker_manager()
            adapter = manager.get_adapter(agent_record.created_by, agent_record.broker_name)
            if not adapter:
                self._log_to_db(db, "warn", f"No broker connected ({agent_record.broker_name})")
                db.commit()
                return

            # Fetch M5 candles — ALWAYS use broker (real-time) for signal generation.
            # Databento has ~2hr delay which is unacceptable for M5 scalping.
            # Databento is only used for chart display, not agent signals.
            data_source = "BROKER"
            candles = await adapter.get_candles(agent_record.symbol, "M5", 500)

            if not candles:
                return

            # Convert to dicts
            bars = [{"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles]

            # Even if no new bar, run reconciliation + max-hold checks once an hour.
            # Otherwise quiet markets (overnight, weekends) skip reconciliation indefinitely.
            import time as _wall
            now_w = _wall.time()
            if now_w - self._last_reconciliation > 3600:
                self._last_reconciliation = now_w
                try:
                    await self._check_closed_trades(adapter, agent_record, db)
                    await self._reconcile_with_broker(adapter, agent_record, db)
                    await self._enforce_max_hold_time(adapter, agent_record, db)
                    db.commit()
                except Exception as e:
                    self._log_to_db(db, "warn", f"Quiet-market reconcile error: {e}")
                    db.rollback()

            # Detect new closed bar
            last_time = bars[-1]["time"] if bars else 0
            if last_time <= self._last_bar_time:
                return  # No new bar

            # Stale-data guard: same time + same OHLC close = broker returned duplicate.
            # This catches the edge case where Oanda serves the same bar twice briefly
            # during heavy load. Compare against the last accepted bar's hash.
            last_bar = bars[-1]
            bar_hash = f"{last_time}:{last_bar['open']:.5f}:{last_bar['high']:.5f}:{last_bar['low']:.5f}:{last_bar['close']:.5f}"
            if bar_hash == self._last_bar_hash:
                return  # Identical bar — skip
            self._last_bar_hash = bar_hash

            self._last_bar_time = last_time
            self._bar_buffer = bars

            # Log data source on first bar (once)
            if not self._warmup_done:
                self._log_to_db(db, "info",
                    f"Data source: {data_source} | {len(bars)} bars loaded for {agent_record.symbol}/M5")

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

            # Periodic reconciliation: every hour, compare DB open trades vs broker positions.
            # Catches trades the user manually closed on the broker side.
            import time as _wall
            now_w = _wall.time()
            if now_w - self._last_reconciliation > 3600:
                self._last_reconciliation = now_w
                await self._reconcile_with_broker(adapter, agent_record, db)

            # Max hold time: close any trade that's been open too long with negligible P&L.
            await self._enforce_max_hold_time(adapter, agent_record, db)

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

                # Per-agent position limit: count only THIS agent's open trades
                open_count = db.query(AgentTrade).filter(
                    AgentTrade.agent_id == self.agent_id,
                    AgentTrade.status == "open",
                ).count()
                max_open = (agent_record.risk_config or {}).get("max_positions", 6)
                if open_count >= max_open:
                    self._log_to_db(db, "info", f"Portfolio limit: {open_count}/{max_open} positions open — skipping trade")
                else:
                    await self._create_trade(signal, adapter, agent_record, db)
            else:
                # Log rejection evaluation — include prediction distribution if available
                last_pred = getattr(self._agent, "_last_prediction", None)
                pred_str = ""
                if isinstance(last_pred, dict):
                    pred_str = (
                        f" | pred: buy={last_pred.get('buy_prob', 0):.3f} "
                        f"hold={last_pred.get('hold_prob', 0):.3f} "
                        f"sell={last_pred.get('sell_prob', 0):.3f}"
                    )
                self._log_to_db(db, "eval",
                    f"Eval #{self._eval_count}: no signal | "
                    f"bars={len(self._bar_buffer)}, balance={balance:.2f}{pred_str}")

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

    async def _reconcile_with_broker(self, adapter, agent_record, db: Session):
        """
        Periodic reconciliation (hourly):
          - If a DB-open trade's broker_ticket is NOT on the broker, mark as closed (missing).
          - If a broker position exists without a matching DB trade, log a CRITICAL orphan.
        """
        try:
            db_open = (
                db.query(AgentTrade)
                .filter(AgentTrade.agent_id == self.agent_id, AgentTrade.status == "open")
                .all()
            )
            broker_positions = await adapter.get_positions()
            broker_tickets = {str(p.id) for p in broker_positions if hasattr(p, "id")}

            # DB-open but not on broker → close as "RECONCILED"
            for t in db_open:
                if t.broker_ticket and str(t.broker_ticket) not in broker_tickets:
                    # Re-check via broker (already done in _check_closed_trades on each poll)
                    # If we reach here and broker really doesn't have it, mark as reconciled.
                    self._log_to_db(db, "warn",
                        f"RECONCILE: DB trade {t.id} ({t.direction} {t.symbol}, "
                        f"ticket {t.broker_ticket}) not found on broker — marking closed")
                    t.status = "closed"
                    t.exit_reason = "RECONCILED"
                    t.exit_time = datetime.now(timezone.utc)
                    t.pnl = t.pnl or 0

            # Broker position without matching DB trade → orphan alert
            db_tickets = {str(t.broker_ticket) for t in db_open if t.broker_ticket}
            # Only check positions matching THIS agent's symbol
            for p in broker_positions:
                if p.symbol != agent_record.symbol:
                    continue
                ticket = str(p.id) if hasattr(p, "id") else ""
                if ticket and ticket not in db_tickets:
                    self._log_to_db(db, "error",
                        f"RECONCILE: Orphan broker position {ticket} ({p.direction} {p.symbol}) "
                        f"has no DB record. Manual intervention required.")
        except Exception as e:
            self._log_to_db(db, "warn", f"Reconciliation error (non-fatal): {e}")

    async def _enforce_max_hold_time(self, adapter, agent_record, db: Session):
        """
        Close trades that have been open beyond the configured max_hold_hours.
        Default: 24 hours. Prevents capital being stuck on stale, range-bound trades.
        """
        cfg = agent_record.risk_config or {}
        max_hold_hours = cfg.get("max_hold_hours", 24)
        if max_hold_hours <= 0:
            return  # disabled

        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_hold_hours)
        stale = (
            db.query(AgentTrade)
            .filter(
                AgentTrade.agent_id == self.agent_id,
                AgentTrade.status == "open",
                AgentTrade.entry_time < cutoff,
            )
            .all()
        )
        for t in stale:
            if not t.broker_ticket:
                continue
            try:
                close_result = await adapter.close_position(t.broker_ticket)
                if close_result and getattr(close_result, "success", False):
                    t.status = "closed"
                    t.exit_reason = "MAX_HOLD_TIME"
                    t.exit_time = datetime.now(timezone.utc)
                    t.pnl = getattr(close_result, "pnl", 0) or 0
                    t.broker_pnl = t.pnl
                    self._log_to_db(db, "trade",
                        f"CLOSED (max hold {max_hold_hours}h) {t.direction} {t.symbol} | "
                        f"P&L: ${t.pnl:.2f} | Ticket: {t.broker_ticket}")
                    if self._active_direction == t.direction:
                        self._active_direction = None
                else:
                    # Close returned failure — try to reconcile from broker history.
                    await self._reconcile_closed_trade_from_broker(adapter, t, db, reason="MAX_HOLD_RECONCILED")
            except Exception as e:
                self._log_to_db(db, "warn", f"Max-hold close failed for {t.broker_ticket}: {e}")
                await self._reconcile_closed_trade_from_broker(adapter, t, db, reason="MAX_HOLD_RECONCILED")

    async def _reconcile_closed_trade_from_broker(self, adapter, trade, db, reason: str):
        """
        When close_position() fails, the broker may have already closed the trade.
        Check broker positions — if not there, attempt to fetch realized PnL from
        the broker's trade history (Oanda-specific) before marking as closed.
        """
        try:
            broker_positions = await adapter.get_positions()
            tickets = {str(p.id) for p in broker_positions if hasattr(p, "id")}
            if str(trade.broker_ticket) in tickets:
                return  # still open on broker — leave for next attempt

            # Attempt to fetch realized PnL from Oanda trade endpoint.
            # Other brokers don't have this API — fall back to 0.
            actual_pnl = None
            actual_exit = None
            try:
                if hasattr(adapter, "_request") and hasattr(adapter, "_account_id"):
                    data = await adapter._request(
                        "GET",
                        f"/v3/accounts/{adapter._account_id}/trades/{trade.broker_ticket}",
                    )
                    oanda_trade = data.get("trade", {})
                    if oanda_trade.get("state") == "CLOSED":
                        actual_pnl = float(oanda_trade.get("realizedPL", 0))
                        actual_exit = float(oanda_trade.get("averageClosePrice", 0)) or None
            except Exception:
                pass

            trade.status = "closed"
            trade.exit_reason = reason
            trade.exit_time = datetime.now(timezone.utc)
            if actual_pnl is not None:
                trade.pnl = actual_pnl
                trade.broker_pnl = actual_pnl
            if actual_exit is not None:
                trade.exit_price = actual_exit
            self._log_to_db(db, "warn",
                f"Reconciled ({reason}) {trade.direction} {trade.symbol} ticket {trade.broker_ticket} — "
                f"P&L: ${(actual_pnl or 0):.2f}")
        except Exception as e:
            self._log_to_db(db, "warn", f"Reconcile fetch failed for {trade.broker_ticket}: {e}")

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
                    now = datetime.now(timezone.utc)

                    # Verify this trade actually existed on the broker
                    # by checking if broker_ticket is a valid transaction ID
                    if not trade.broker_ticket:
                        # No ticket = order was never confirmed. Delete ghost trade.
                        self._log_to_db(db, "warn",
                            f"GHOST TRADE removed: {trade.direction} {trade.symbol} — no broker ticket")
                        db.delete(trade)
                        continue

                    # Try to get actual P&L from Oanda closed trade history
                    actual_pnl = None
                    actual_exit = None
                    exit_reason = "closed"
                    try:
                        # Check Oanda for the actual trade details
                        data = await adapter._request(
                            "GET",
                            f"/v3/accounts/{adapter._account_id}/trades/{trade.broker_ticket}"
                        )
                        oanda_trade = data.get("trade", {})
                        state = oanda_trade.get("state", "")

                        if state == "OPEN":
                            # Trade is actually still open on Oanda — don't close it
                            continue
                        elif state == "CLOSED":
                            actual_pnl = float(oanda_trade.get("realizedPL", 0))
                            actual_exit = float(oanda_trade.get("averageClosePrice", 0))
                            # Determine exit reason
                            close_time = oanda_trade.get("closeTime", "")
                            if trade.take_profit and actual_exit:
                                if (trade.direction == "BUY" and actual_exit >= trade.take_profit) or \
                                   (trade.direction == "SELL" and actual_exit <= trade.take_profit):
                                    exit_reason = "TP_HIT"
                            if trade.stop_loss and actual_exit:
                                if (trade.direction == "BUY" and actual_exit <= trade.stop_loss) or \
                                   (trade.direction == "SELL" and actual_exit >= trade.stop_loss):
                                    exit_reason = "SL_HIT"
                        else:
                            # Unknown state — mark as cancelled
                            exit_reason = "CANCELLED"
                            actual_pnl = 0
                    except Exception:
                        # Can't verify with Oanda — mark as unknown, don't fabricate P&L
                        exit_reason = "UNKNOWN"
                        actual_pnl = 0

                    trade.status = "closed"
                    trade.exit_time = now
                    trade.exit_reason = exit_reason
                    trade.exit_price = actual_exit or 0
                    trade.pnl = actual_pnl if actual_pnl is not None else 0
                    trade.broker_pnl = actual_pnl

                    # Analytics: compute trade duration
                    if trade.entry_time:
                        delta = now - trade.entry_time
                        trade.time_to_exit_seconds = int(delta.total_seconds())
                        trade.bars_to_exit = int(delta.total_seconds() / 300)  # M5 bars

                    pnl_str = f"P&L:{trade.pnl}"
                    self._log_to_db(db, "trade",
                        f"CLOSED {trade.direction} {trade.symbol} | {exit_reason} | "
                        f"Entry:{trade.entry_price} Exit:{trade.exit_price} | {pnl_str} | "
                        f"Ticket:{trade.broker_ticket}",
                    )

                    if trade.broker_pnl is not None:
                        self._daily_pnl += trade.broker_pnl

                    if self._active_direction == trade.direction:
                        self._active_direction = None

                    # Notify agent's RiskManager (if prop-firm mode on)
                    try:
                        if self._agent and hasattr(self._agent, "on_position_closed"):
                            self._agent.on_position_closed(trade.pnl or 0)
                    except Exception:
                        pass

                    # ── AI monitoring hook: trade closed ──
                    try:
                        from app.services.llm.monitoring import on_trade_closed, detect_and_alert
                        trade_dict = {
                            "symbol": trade.symbol,
                            "direction": trade.direction,
                            "entry_price": trade.entry_price,
                            "exit_price": trade.exit_price,
                            "stop_loss": trade.stop_loss,
                            "take_profit": trade.take_profit,
                            "lot_size": trade.lot_size,
                            "pnl": trade.pnl,
                            "exit_reason": trade.exit_reason,
                            "confidence": trade.confidence,
                            "session_name": trade.session_name,
                        }
                        agent_dict = {
                            "id": agent_record.id,
                            "symbol": agent_record.symbol,
                            "agent_type": agent_record.agent_type,
                            "name": agent_record.name,
                        }
                        # Fire-and-forget — don't block the poll loop on LLM latency
                        import asyncio
                        asyncio.create_task(on_trade_closed(agent_record.created_by, trade_dict, agent_dict))
                        asyncio.create_task(detect_and_alert(agent_record.created_by, agent_record.id))
                    except Exception as hook_err:
                        self._log_to_db(db, "warn", f"Monitoring hook error: {hook_err}")

        except Exception as e:
            self._log_to_db(db, "warn", f"Position check error: {e}")

    async def _create_trade(self, signal: dict, adapter, agent_record, db: Session):
        """
        Execute trade via broker and record in DB.

        Safety (Batch A): if the broker confirms but the DB commit later fails,
        the broker ticket is logged to stdout for manual reconciliation. The
        orphan-detection logic in main.py lifespan will also catch these on the
        next restart.
        """
        direction = signal["direction"]

        # Check active direction (no duplicate positions)
        if self._active_direction == direction:
            return

        self._signal_count += 1

        reason = signal.get("reason", "unknown")
        self._log_to_db(db, "signal",
            f"PLACING {direction} {agent_record.symbol} | "
            f"Entry:{signal['entry_price']} SL:{signal['stop_loss']} TP:{signal['take_profit']} | "
            f"Conf:{signal['confidence']:.3f} | Size:{signal['lot_size']} | Model:{reason}",
            signal,
        )

        # Pre-trade margin check — avoid hitting broker rate limits with guaranteed rejections
        try:
            acct = await adapter.get_account_info()
            margin_avail = getattr(acct, "margin_available", 0) or 0
            # Estimate required margin conservatively: position notional × 2% (50× leverage).
            # This is a ballpark; real Oanda margin depends on instrument tier.
            notional_estimate = float(signal.get("lot_size", 0)) * float(signal.get("entry_price", 0))
            required_margin = notional_estimate * 0.02
            if margin_avail > 0 and required_margin > margin_avail * 0.95:
                self._log_to_db(db, "warn",
                    f"Insufficient margin: need ~${required_margin:.2f}, have ${margin_avail:.2f} — skipping trade")
                return
        except Exception:
            pass  # Non-fatal — proceed and let the broker decide

        # Place order through broker
        broker_ticket = None
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
                broker_ticket = result.order_id
                self._active_direction = direction

                # Execution quality tracking: compute slippage in pips
                requested_price = getattr(result, "requested_price", 0) or signal["entry_price"]
                fill_price = getattr(result, "fill_price", 0) or signal["entry_price"]
                slippage_pips = None
                try:
                    from app.services.agent.instrument_specs import get_spec
                    spec = get_spec(agent_record.symbol)
                    if spec and spec.pip_size > 0 and fill_price and requested_price:
                        diff = abs(fill_price - requested_price)
                        slippage_pips = round(diff / spec.pip_size, 2)
                except Exception:
                    pass

                # Use fill_price as the actual entry (backtest-parity: no silent discount)
                actual_entry = fill_price if fill_price > 0 else signal["entry_price"]

                trade = AgentTrade(
                    agent_id=self.agent_id,
                    symbol=agent_record.symbol,
                    direction=direction,
                    entry_price=actual_entry,
                    stop_loss=signal["stop_loss"],
                    take_profit=signal["take_profit"],
                    lot_size=signal["lot_size"],
                    status="open",
                    confidence=signal["confidence"],
                    signal_data=signal,
                    entry_time=datetime.now(timezone.utc),
                    broker_ticket=broker_ticket,
                    # Execution quality
                    requested_price=requested_price,
                    fill_price=fill_price,
                    slippage_pips=slippage_pips,
                    # Analytics enrichment
                    mtf_score=signal.get("mtf_score"),
                    mtf_layers=signal.get("mtf_layers"),
                    session_name=signal.get("session_name"),
                    top_features=signal.get("top_features"),
                    atr_at_entry=signal.get("atr_at_entry"),
                    model_name=signal.get("model_name") or reason,
                )
                db.add(trade)
                self._daily_trade_count += 1

                # Notify agent's RiskManager (if prop-firm mode on)
                try:
                    if self._agent and hasattr(self._agent, "on_position_opened"):
                        self._agent.on_position_opened()
                except Exception:
                    pass

                self._log_to_db(db, "trade",
                    f"OPENED {direction} {agent_record.symbol} @ {signal['entry_price']} | "
                    f"SL:{signal['stop_loss']} TP:{signal['take_profit']} | "
                    f"Size:{signal['lot_size']} | Ticket:{broker_ticket} | "
                    f"Conf:{signal['confidence']:.3f} | Model:{reason}",
                    signal,
                )

                # ── AI monitoring hook: trade opened ──
                try:
                    from app.services.llm.monitoring import on_trade_opened
                    trade_dict = {
                        "symbol": agent_record.symbol,
                        "direction": direction,
                        "entry_price": signal["entry_price"],
                        "stop_loss": signal["stop_loss"],
                        "take_profit": signal["take_profit"],
                        "lot_size": signal["lot_size"],
                        "confidence": signal["confidence"],
                    }
                    agent_dict = {
                        "id": agent_record.id,
                        "symbol": agent_record.symbol,
                        "agent_type": agent_record.agent_type,
                        "name": agent_record.name,
                    }
                    import asyncio
                    asyncio.create_task(on_trade_opened(agent_record.created_by, trade_dict, agent_dict))
                except Exception:
                    pass
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
            # AI diagnosis of execution errors (rate-limited)
            try:
                from app.services.llm.monitoring import on_error
                agent_dict = {
                    "id": agent_record.id,
                    "symbol": agent_record.symbol,
                    "agent_type": agent_record.agent_type,
                    "name": agent_record.name,
                }
                asyncio.create_task(on_error(
                    agent_record.created_by, agent_record.id,
                    f"Execution failed ({direction} {agent_record.symbol}): {e}",
                    agent_dict, error_kind="execution"
                ))
            except Exception:
                pass
            # If the broker confirmed but we crashed before DB commit, log
            # CRITICAL so Sentry + log aggregators surface it as an alert.
            if broker_ticket:
                logger.critical(
                    f"Broker confirmed ticket={broker_ticket} for "
                    f"{direction} {agent_record.symbol} but DB commit may have failed. "
                    f"Check broker for orphaned position."
                )

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

    def reload_agent_config(self, agent_id: int) -> bool:
        """
        Reload a running agent's config from the DB.
        Called after Edit Config saves changes so the live agent picks up
        new risk_per_trade, max_daily_loss_pct, cooldown_bars, etc.
        """
        runner = self._runners.get(agent_id)
        if not runner or not runner._agent:
            return False

        db = SessionLocal()
        try:
            record = db.query(TradingAgent).filter(TradingAgent.id == agent_id).first()
            if not record:
                return False

            new_config = {
                **(record.risk_config or {}),
                "timeframe": record.timeframe or "M5",
            }
            agent = runner._agent
            agent.config = new_config

            # Rebuild risk_config dict from the new config.
            # Default 0.001 (0.10%) — any fallback here means the user intended
            # a real value and we'd rather log a warning than silently 10x.
            risk_per_trade = new_config.get("risk_per_trade")
            if risk_per_trade is None:
                risk_per_trade = 0.001
                if hasattr(agent, "_log_fn") and agent._log_fn:
                    agent._log_fn("warn",
                        "Config reload: risk_per_trade missing, using 0.10% default")
            agent.risk_config = {
                "max_drawdown_pct": new_config.get("max_drawdown_pct", 0.10),
                "daily_loss_limit_pct": new_config.get("max_daily_loss_pct", 0.03),
                "risk_per_trade_pct": risk_per_trade,
                "max_trades_per_day": new_config.get("max_trades_per_day", 10),
            }
            agent.cooldown_bars = new_config.get("cooldown_bars", 3)
            agent.session_filter = new_config.get("session_filter", False)
            agent.news_filter = new_config.get("news_filter_enabled", False)

            # Reset peak equity so the new drawdown limit applies fresh from current
            # balance, not from a pre-config-change high-water mark.
            if hasattr(agent, "_peak_equity"):
                agent._peak_equity = 0.0

            # Re-initialize RiskManager if prop-firm mode changed or thresholds updated.
            # Without this, the agent's RiskManager instance keeps the old thresholds
            # even after the user edits risk config in the UI.
            prop_firm = new_config.get("prop_firm_enabled", False)
            agent.prop_firm_enabled = prop_firm
            if prop_firm:
                try:
                    from app.services.agent.risk_manager import RiskManager
                    override_keys = [
                        "max_daily_dd_pct", "max_total_dd_pct", "daily_dd_yellow",
                        "daily_dd_red", "daily_dd_hard_stop", "max_trades_per_day",
                        "max_concurrent_positions", "base_risk_per_trade_pct",
                    ]
                    overrides = {k: new_config[k] for k in override_keys if k in new_config}
                    agent._risk_manager = RiskManager(config=overrides)
                    if hasattr(agent, "_log_fn") and agent._log_fn:
                        agent._log_fn("info", "RiskManager re-initialized with new config")
                except Exception as rm_err:
                    if hasattr(agent, "_log_fn") and agent._log_fn:
                        agent._log_fn("warn", f"RiskManager re-init failed: {rm_err}")
            else:
                agent._risk_manager = None

            # Log the reload for audit trail
            if hasattr(agent, "_log_fn") and agent._log_fn:
                max_lot = new_config.get("max_lot_size")
                sizing_mode = new_config.get("sizing_mode", "risk_pct")
                agent._log_fn(
                    "info",
                    f"Config reloaded: risk={agent.risk_config['risk_per_trade_pct']*100:.2f}%, "
                    f"daily_loss={agent.risk_config['daily_loss_limit_pct']*100:.1f}%, "
                    f"cooldown={agent.cooldown_bars}, "
                    f"sizing_mode={sizing_mode}, max_lot={max_lot}",
                )
            return True
        except Exception as e:
            logger.warning(f"Failed to reload config for agent {agent_id}: {e}", exc_info=True)
            return False
        finally:
            db.close()

    def reload_models_for_symbol(self, symbol: str):
        """
        Hot-reload models for all running agents trading a specific symbol.

        Supports every agent type:
        - PotentialAgent and FlowrexAgentV2 expose a parameterless `load()` method
          that rebuilds self.models from disk — preferred path.
        - Legacy FlowrexAgent uses _ensemble_scalping / _ensemble_expert — fallback.
        """
        reloaded = 0
        for runner in self._runners.values():
            agent = runner._agent
            if not agent or getattr(agent, "symbol", None) != symbol:
                continue
            try:
                if hasattr(agent, "load") and callable(agent.load):
                    # Modern agents: PotentialAgent, FlowrexAgentV2
                    if agent.load():
                        reloaded += 1
                        # Clear feature cache — old features may mismatch new model shape
                        for attr in ("_feature_cache_key", "_feature_cache_result"):
                            if hasattr(agent, attr):
                                setattr(agent, attr, None)
                        if hasattr(agent, "_log_fn") and agent._log_fn:
                            agent._log_fn("info", f"Models hot-reloaded for {symbol}")
                elif hasattr(agent, "_ensemble_scalping"):
                    # Legacy FlowrexAgent
                    agent._ensemble_scalping.load_models()
                    reloaded += 1
                elif hasattr(agent, "_ensemble_expert"):
                    agent._ensemble_expert.load_models()
                    reloaded += 1
            except Exception as e:
                logger.warning(f"reload_models_for_symbol failed for agent {runner.agent_id}: {e}", exc_info=True)
        return reloaded


# Singleton
_engine: Optional[AlgoEngine] = None


def get_algo_engine() -> AlgoEngine:
    global _engine
    if _engine is None:
        _engine = AlgoEngine()
    return _engine
