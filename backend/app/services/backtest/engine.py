"""
Backtesting engine — realistic simulation with spread, slippage, commission.
Includes Monte Carlo analysis for drawdown confidence intervals.
"""
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

from app.services.ml.features_mtf import compute_expert_features
from app.services.ml.ensemble_engine import EnsembleSignalEngine
from app.services.agent.instrument_specs import calc_sl_tp, calc_lot_size, get_spec
from app.services.agent.risk_manager import RiskManager
from app.services.ml.symbol_config import get_symbol_config


@dataclass
class TransactionCosts:
    """Per-trade cost model."""
    spread_pips: float = 0.0
    slippage_pips: float = 0.0
    commission_per_lot: float = 0.0


@dataclass
class BacktestTrade:
    entry_bar: int = 0
    exit_bar: int = 0
    direction: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    lot_size: float = 0.0
    gross_pnl: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    commission: float = 0.0
    pnl: float = 0.0        # net P&L after all costs
    exit_reason: str = ""
    confidence: float = 0.0
    entry_time: int = 0
    exit_time: int = 0
    duration_bars: int = 0


@dataclass
class MonteCarloResult:
    drawdown_95th: float = 0.0
    drawdown_99th: float = 0.0
    worst_drawdown: float = 0.0
    median_pnl: float = 0.0
    pnl_5th: float = 0.0
    pnl_95th: float = 0.0
    simulations: int = 0


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    # P&L
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    total_spread_cost: float = 0.0
    total_slippage_cost: float = 0.0
    total_commission: float = 0.0
    total_costs: float = 0.0
    # Stats
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    expectancy: float = 0.0
    risk_reward_ratio: float = 0.0
    calmar_ratio: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_trade_duration_bars: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    # Curves
    equity_curve: list[dict] = field(default_factory=list)
    drawdown_curve: list[dict] = field(default_factory=list)
    monthly_returns: dict = field(default_factory=dict)
    # Monte Carlo
    monte_carlo: Optional[MonteCarloResult] = None


class BacktestEngine:
    """Realistic backtesting with transaction costs and Monte Carlo."""

    def run(
        self,
        symbol: str,
        timeframe: str = "M5",
        agent_type: str = "scalping",
        risk_config: dict = None,
        m5_data: pd.DataFrame = None,
        h1_data: pd.DataFrame = None,
        spread_pips: float = None,
        slippage_pips: float = None,
        commission_per_lot: float = 0.0,
        prime_hours_only: bool = True,
        include_monte_carlo: bool = True,
        starting_balance: float = 10000.0,
    ) -> BacktestResult:
        if m5_data is None or len(m5_data) < 200:
            return BacktestResult()

        # Symbol config for defaults
        sym_config = get_symbol_config(symbol)
        spec = get_spec(symbol)

        # Transaction costs (use provided or symbol defaults)
        costs = TransactionCosts(
            spread_pips=spread_pips if spread_pips is not None else sym_config.get("spread_pips", 1.0),
            slippage_pips=slippage_pips if slippage_pips is not None else sym_config.get("spread_pips", 1.0) * 0.3,
            commission_per_lot=commission_per_lot,
        )
        pip_size = spec.pip_size
        pip_value = spec.pip_value

        # Convert pips to price
        half_spread = costs.spread_pips * pip_size / 2
        slippage_price = costs.slippage_pips * pip_size

        risk_config = risk_config or {"risk_per_trade": 0.005, "max_daily_loss_pct": 0.04, "cooldown_bars": 3}
        risk_manager = RiskManager(risk_config)

        # Load ensemble
        ensemble = EnsembleSignalEngine(symbol, agent_type)
        if not ensemble.load_models():
            return BacktestResult()

        # Compute features
        feature_names, X = compute_expert_features(m5_data, h1_data)
        closes = m5_data["close"].values
        highs = m5_data["high"].values
        lows = m5_data["low"].values
        times = m5_data["time"].values if "time" in m5_data.columns else np.arange(len(closes))

        atr_idx = feature_names.index("atr_14") if "atr_14" in feature_names else None

        # Prime hours
        prime_start, prime_end = sym_config.get("prime_hours_utc", (0, 24))

        # Simulation state
        balance = starting_balance
        trades: list[BacktestTrade] = []
        equity_curve = [{"time": int(times[0]), "pnl": 0.0}]
        open_trade: Optional[BacktestTrade] = None
        last_trade_bar = -999
        daily_pnl = 0.0
        daily_trade_count = 0
        cum_pnl = 0.0
        last_day = -1

        warmup = 200
        for i in range(warmup, len(closes)):
            # Daily reset
            try:
                current_day = datetime.fromtimestamp(int(times[i]), tz=timezone.utc).day
            except (ValueError, OSError):
                current_day = i // 288  # fallback: ~288 M5 bars per day
            if current_day != last_day:
                daily_pnl = 0.0
                daily_trade_count = 0
                last_day = current_day

            # Check open trade SL/TP
            if open_trade:
                exit_price = None
                exit_reason = ""

                if open_trade.direction == "BUY":
                    # SL check: low touches SL → fill at SL - slippage (worse fill)
                    if lows[i] <= open_trade.stop_loss:
                        exit_price = open_trade.stop_loss - slippage_price
                        exit_reason = "SL"
                    elif highs[i] >= open_trade.take_profit:
                        exit_price = open_trade.take_profit - half_spread  # spread on exit
                        exit_reason = "TP"
                else:
                    if highs[i] >= open_trade.stop_loss:
                        exit_price = open_trade.stop_loss + slippage_price
                        exit_reason = "SL"
                    elif lows[i] <= open_trade.take_profit:
                        exit_price = open_trade.take_profit + half_spread
                        exit_reason = "TP"

                if exit_price is not None:
                    open_trade = self._close_trade(
                        open_trade, exit_price, exit_reason, i, int(times[i]),
                        pip_size, pip_value, costs,
                    )
                    trades.append(open_trade)
                    cum_pnl += open_trade.pnl
                    daily_pnl += open_trade.pnl
                    balance += open_trade.pnl
                    equity_curve.append({"time": int(times[i]), "pnl": round(cum_pnl, 2)})
                    open_trade = None
                    continue

            if open_trade:
                continue

            # Prime hours filter
            if prime_hours_only:
                try:
                    hour = datetime.fromtimestamp(int(times[i]), tz=timezone.utc).hour
                except (ValueError, OSError):
                    hour = (i * 5 // 60) % 24
                if not (prime_start <= hour < prime_end):
                    continue

            # Risk check
            bars_since = i - last_trade_bar
            risk_check = risk_manager.check_trade(balance, daily_pnl, daily_trade_count, bars_since)
            if not risk_check.approved:
                continue

            # Predict
            feature_vector = X[i]
            signal = ensemble.predict(feature_vector)
            if signal is None or signal.direction == 0:
                continue

            # SL/TP
            atr_val = float(X[i, atr_idx]) if atr_idx is not None else 0
            if atr_val <= 0:
                continue

            sl_mult = 1.5 if agent_type == "scalping" else 2.0
            tp_mult = 2.5 if agent_type == "scalping" else 3.0
            sl, tp = calc_sl_tp(closes[i], signal.direction, atr_val, sl_mult, tp_mult)
            sl_dist = abs(closes[i] - sl)
            lot_size = calc_lot_size(symbol, risk_check.risk_amount, sl_dist)

            # Apply spread + slippage to entry
            direction_str = "BUY" if signal.direction == 1 else "SELL"
            if direction_str == "BUY":
                entry_price = closes[i] + half_spread + slippage_price
            else:
                entry_price = closes[i] - half_spread - slippage_price

            open_trade = BacktestTrade(
                entry_bar=i,
                direction=direction_str,
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                lot_size=lot_size,
                confidence=signal.confidence,
                entry_time=int(times[i]),
            )
            last_trade_bar = i
            daily_trade_count += 1

        # Close any open trade at last bar
        if open_trade:
            exit_price = closes[-1] - half_spread if open_trade.direction == "BUY" else closes[-1] + half_spread
            open_trade = self._close_trade(
                open_trade, exit_price, "EOD", len(closes) - 1, int(times[-1]),
                pip_size, pip_value, costs,
            )
            trades.append(open_trade)
            cum_pnl += open_trade.pnl

        result = self._compute_result(trades, equity_curve, times, starting_balance)

        # Monte Carlo
        if include_monte_carlo and len(trades) >= 10:
            result.monte_carlo = self._monte_carlo(trades, n_simulations=1000)

        return result

    def _close_trade(
        self, trade: BacktestTrade, exit_price: float, reason: str,
        bar: int, time: int, pip_size: float, pip_value: float,
        costs: TransactionCosts,
    ) -> BacktestTrade:
        """Close a trade with proper P&L calculation including costs."""
        trade.exit_price = exit_price
        trade.exit_reason = reason
        trade.exit_bar = bar
        trade.exit_time = time
        trade.duration_bars = bar - trade.entry_bar

        # Pip-based P&L
        if trade.direction == "BUY":
            pip_distance = (exit_price - trade.entry_price) / pip_size
        else:
            pip_distance = (trade.entry_price - exit_price) / pip_size

        trade.gross_pnl = pip_distance * pip_value * trade.lot_size

        # Costs
        spread_cost_pips = costs.spread_pips  # already applied to entry/exit prices
        trade.spread_cost = spread_cost_pips * pip_value * trade.lot_size
        trade.slippage_cost = costs.slippage_pips * pip_value * trade.lot_size
        trade.commission = costs.commission_per_lot * trade.lot_size * 2  # round-trip

        # Net P&L = gross - commission (spread/slippage already in price)
        trade.pnl = trade.gross_pnl - trade.commission

        return trade

    def _compute_result(self, trades: list[BacktestTrade], equity_curve: list[dict],
                        times: np.ndarray, starting_balance: float) -> BacktestResult:
        if not trades:
            return BacktestResult(equity_curve=equity_curve)

        pnls = [t.pnl for t in trades]
        gross_pnls = [t.gross_pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        net_pnl = sum(pnls)
        gross_pnl = sum(gross_pnls)
        total_spread = sum(t.spread_cost for t in trades)
        total_slippage = sum(t.slippage_cost for t in trades)
        total_commission = sum(t.commission for t in trades)
        total_costs = total_spread + total_slippage + total_commission

        win_rate = len(wins) / len(pnls) * 100 if pnls else 0
        gross_profit = sum(wins) if wins else 0
        gross_loss_val = abs(sum(losses)) if losses else 0
        profit_factor = gross_profit / gross_loss_val if gross_loss_val > 0 else float("inf") if gross_profit > 0 else 0

        avg_win = float(np.mean(wins)) if wins else 0
        avg_loss = float(np.mean(losses)) if losses else 0

        # Sharpe
        sharpe = 0.0
        if len(pnls) > 1 and np.std(pnls) > 0:
            sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(252))

        # Drawdown curve
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        dd = peak - cumulative
        max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0

        drawdown_curve = []
        for j, t in enumerate(trades):
            drawdown_curve.append({"time": t.exit_time, "drawdown": round(float(dd[j]), 2)})

        # Expectancy
        win_prob = len(wins) / len(pnls) if pnls else 0
        loss_prob = len(losses) / len(pnls) if pnls else 0
        expectancy = (avg_win * win_prob) + (avg_loss * loss_prob)

        # Risk:reward
        risk_reward = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        # Calmar ratio (annualized return / max drawdown)
        calmar = 0.0
        if max_dd > 0 and len(trades) > 0:
            # Estimate annual return
            total_bars = trades[-1].exit_bar - trades[0].entry_bar if len(trades) > 1 else 1
            annual_factor = (252 * 288) / max(total_bars, 1)  # 288 M5 bars/day
            annual_return = net_pnl * annual_factor / starting_balance
            calmar = annual_return / (max_dd / starting_balance) if max_dd > 0 else 0

        # Streaks
        max_win_streak, max_loss_streak = self._compute_streaks(pnls)

        # Trade duration
        durations = [t.duration_bars for t in trades]
        avg_duration = float(np.mean(durations)) if durations else 0

        # Monthly returns
        monthly = {}
        for t in trades:
            try:
                dt = datetime.fromtimestamp(t.exit_time, tz=timezone.utc)
                key = dt.strftime("%Y-%m")
            except (ValueError, OSError):
                key = "unknown"
            monthly[key] = monthly.get(key, 0) + t.pnl
        monthly = {k: round(v, 2) for k, v in sorted(monthly.items())}

        return BacktestResult(
            trades=trades,
            gross_pnl=round(gross_pnl, 2),
            net_pnl=round(net_pnl, 2),
            total_spread_cost=round(total_spread, 2),
            total_slippage_cost=round(total_slippage, 2),
            total_commission=round(total_commission, 2),
            total_costs=round(total_costs, 2),
            win_rate=round(win_rate, 2),
            profit_factor=round(profit_factor, 4),
            max_drawdown=round(max_dd, 2),
            sharpe_ratio=round(sharpe, 4),
            expectancy=round(expectancy, 2),
            risk_reward_ratio=round(risk_reward, 2),
            calmar_ratio=round(calmar, 4),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            avg_trade_duration_bars=round(avg_duration, 1),
            max_consecutive_wins=max_win_streak,
            max_consecutive_losses=max_loss_streak,
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            monthly_returns=monthly,
        )

    def _compute_streaks(self, pnls: list[float]) -> tuple[int, int]:
        max_win = max_loss = win = loss = 0
        for p in pnls:
            if p > 0:
                win += 1; loss = 0; max_win = max(max_win, win)
            elif p < 0:
                loss += 1; win = 0; max_loss = max(max_loss, loss)
            else:
                win = loss = 0
        return max_win, max_loss

    def _monte_carlo(self, trades: list[BacktestTrade], n_simulations: int = 1000) -> MonteCarloResult:
        """Shuffle trade order N times to get drawdown confidence intervals."""
        pnls = np.array([t.pnl for t in trades])
        max_drawdowns = []
        final_pnls = []

        for _ in range(n_simulations):
            shuffled = np.random.permutation(pnls)
            cumulative = np.cumsum(shuffled)
            peak = np.maximum.accumulate(cumulative)
            dd = peak - cumulative
            max_drawdowns.append(float(np.max(dd)))
            final_pnls.append(float(cumulative[-1]))

        return MonteCarloResult(
            drawdown_95th=round(float(np.percentile(max_drawdowns, 95)), 2),
            drawdown_99th=round(float(np.percentile(max_drawdowns, 99)), 2),
            worst_drawdown=round(float(np.max(max_drawdowns)), 2),
            median_pnl=round(float(np.median(final_pnls)), 2),
            pnl_5th=round(float(np.percentile(final_pnls, 5)), 2),
            pnl_95th=round(float(np.percentile(final_pnls, 95)), 2),
            simulations=n_simulations,
        )
