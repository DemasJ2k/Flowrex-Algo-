"""
Tests for the Phase-2 swing system: swing_rules primitives, SwingAgent
signal contract, and engine dispatch.
"""
import numpy as np
import pytest
from unittest.mock import patch

from app.services.agent.swing_rules import (
    SwingConfig, Zone, ZoneTracker, check_entry, d1_trend, d1_trend_series,
)


def _cfg(**over):
    """Test config: relaxed displacement so scenarios stay small."""
    base = dict(
        d1_ema_period=5, d1_slope_bars=0,
        displacement_atr_mult=1.5, displacement_span=2,
        ob_only=True, max_touches=1,
        touch_window_h1_bars=3,
        sl_buffer_atr_mult=0.25, min_sl_atr_mult=0.1, max_sl_atr_mult=3.0,
        target_rr=3.0, cooldown_h1_bars=12, atr_period=3,
    )
    base.update(over)
    return SwingConfig(**base)


def _feed(tracker, bars, atr_val=2.0):
    for (o, h, l, c) in bars:
        tracker.on_h4_bar(o, h, l, c, atr_val)


FILLER = [(100.0, 101.0, 99.0, 100.0)] * 5
# Bearish candle then a 2-bar bullish displacement clearing its high:
# span high 104.5 - OB close 99.5 = 5.0 >= 1.5 × ATR(2.0)
DEMAND_OB = [
    (100.5, 101.0, 99.0, 99.5),      # the OB candle (bearish), zone [99, 101]
    (99.5, 102.0, 99.4, 101.8),
    (101.8, 104.5, 101.5, 104.2),    # displacement confirms here
]


# ── ZoneTracker ────────────────────────────────────────────────────────


def test_demand_order_block_detected():
    tracker = ZoneTracker(_cfg())
    _feed(tracker, FILLER + DEMAND_OB)
    zones = tracker.live_zones("demand")
    ob = [z for z in zones if z.source == "ob"]
    assert len(ob) == 1
    assert ob[0].top == 101.0 and ob[0].bottom == 99.0


def test_no_order_block_without_displacement():
    tracker = ZoneTracker(_cfg())
    weak = [
        (100.5, 101.0, 99.0, 99.5),
        (99.5, 101.2, 99.4, 101.1),   # clears high but move is only 1.7 < 1.5×2.0
        (101.1, 101.4, 100.8, 101.2),
    ]
    _feed(tracker, FILLER + weak)
    assert [z for z in tracker.live_zones("demand") if z.source == "ob"] == []


def test_zone_invalidated_on_close_through_far_edge():
    tracker = ZoneTracker(_cfg())
    _feed(tracker, FILLER + DEMAND_OB)
    _feed(tracker, [(101.0, 101.5, 97.0, 98.0)])   # H4 close below 99
    assert tracker.live_zones("demand") == []


def test_zone_spent_after_max_touches():
    tracker = ZoneTracker(_cfg(max_touches=1))
    _feed(tracker, FILLER + DEMAND_OB)
    # touch 1: dips into zone, leaves
    _feed(tracker, [(104.0, 104.5, 100.5, 103.8), (103.8, 105.0, 103.0, 104.5)])
    assert len(tracker.live_zones("demand")) == 1
    # touch 2 kills it
    _feed(tracker, [(104.5, 104.8, 100.7, 104.0)])
    assert tracker.live_zones("demand") == []


def test_fvg_detected_and_suppressed_by_ob_only():
    fvg_bars = FILLER + [(100.0, 100.5, 99.5, 100.2),
                         (100.2, 103.0, 100.1, 102.8),
                         (103.0, 104.0, 102.0, 103.5)]   # low 102 > high[i-2] 100.5
    tracker = ZoneTracker(_cfg(ob_only=False))
    _feed(tracker, fvg_bars)
    assert any(z.source == "fvg" for z in tracker.live_zones("demand"))
    tracker2 = ZoneTracker(_cfg(ob_only=True))
    _feed(tracker2, fvg_bars)
    assert not any(z.source == "fvg" for z in tracker2.live_zones("demand"))


# ── D1 trend ───────────────────────────────────────────────────────────


def test_d1_trend_up_down():
    up = np.linspace(100, 130, 60)
    down = np.linspace(130, 100, 60)
    assert d1_trend(up, ema_period=10) == 1
    assert d1_trend(down, ema_period=10) == -1


def test_d1_slope_filter_zeroes_chop():
    rng = np.random.default_rng(7)
    chop = 100 + np.cumsum(rng.normal(0, 0.01, 120))
    s = d1_trend_series(chop, ema_period=10, slope_bars=5)
    # In near-flat chop the slope filter must produce at least some 0s
    assert (s == 0).sum() > 0


def test_d1_trend_insufficient_history_is_zero():
    assert d1_trend(np.array([1.0, 2.0]), ema_period=50) == 0


# ── check_entry ────────────────────────────────────────────────────────


def _h1_long_setup():
    """H1 series that tags a [99, 101] demand zone then confirms upward."""
    o = np.array([102.0, 101.5, 101.0, 100.6, 101.0])
    h = np.array([102.5, 101.8, 101.3, 101.0, 101.9])
    l = np.array([101.4, 100.9, 100.4, 100.2, 100.8])   # tags zone (low <= 101)
    c = np.array([101.6, 101.1, 100.7, 100.9, 101.7])   # last close > prev high 101.0
    return o, h, l, c


def _tracker_with_demand(cfg):
    tracker = ZoneTracker(cfg)
    _feed(tracker, FILLER + DEMAND_OB)
    return tracker


def test_check_entry_long_fires_with_correct_levels():
    cfg = _cfg()
    tracker = _tracker_with_demand(cfg)
    o, h, l, c = _h1_long_setup()
    sig = check_entry(o, h, l, c, 4, tracker, trend=1, atr_h4_now=2.0, cfg=cfg)
    assert sig is not None and sig["direction"] == 1
    assert sig["stop_loss"] == pytest.approx(99.0 - 0.25 * 2.0)   # zone bottom - buffer
    sl_dist = sig["entry_ref"] - sig["stop_loss"]
    assert sig["take_profit"] == pytest.approx(sig["entry_ref"] + 3.0 * sl_dist)
    assert 0 < sig["confidence"] <= 0.85


def test_check_entry_blocked_against_trend():
    cfg = _cfg()
    tracker = _tracker_with_demand(cfg)
    o, h, l, c = _h1_long_setup()
    assert check_entry(o, h, l, c, 4, tracker, trend=-1, atr_h4_now=2.0, cfg=cfg) is None
    assert check_entry(o, h, l, c, 4, tracker, trend=0, atr_h4_now=2.0, cfg=cfg) is None


def test_check_entry_blocked_by_cooldown():
    cfg = _cfg(cooldown_h1_bars=12)
    tracker = _tracker_with_demand(cfg)
    o, h, l, c = _h1_long_setup()
    assert check_entry(o, h, l, c, 4, tracker, trend=1, atr_h4_now=2.0, cfg=cfg,
                       last_entry_i=0) is None


def test_check_entry_requires_micro_bos():
    cfg = _cfg()
    tracker = _tracker_with_demand(cfg)
    o, h, l, c = _h1_long_setup()
    c = c.copy()
    c[4] = 100.9   # no break of previous high
    assert check_entry(o, h, l, c, 4, tracker, trend=1, atr_h4_now=2.0, cfg=cfg) is None


def test_check_entry_enforces_sl_bounds():
    cfg = _cfg(max_sl_atr_mult=0.5)   # zone SL distance will exceed 0.5×ATR
    tracker = _tracker_with_demand(cfg)
    o, h, l, c = _h1_long_setup()
    assert check_entry(o, h, l, c, 4, tracker, trend=1, atr_h4_now=2.0, cfg=cfg) is None


def test_zone_consumed_after_entry():
    cfg = _cfg()
    tracker = _tracker_with_demand(cfg)
    o, h, l, c = _h1_long_setup()
    assert check_entry(o, h, l, c, 4, tracker, trend=1, atr_h4_now=2.0, cfg=cfg) is not None
    # Same setup again: the zone is consumed, no second trade off it
    assert check_entry(o, h, l, c, 4, tracker, trend=1, atr_h4_now=2.0, cfg=cfg) is None


# ── SwingAgent contract ────────────────────────────────────────────────


class _Candle:
    def __init__(self, t, o, h, l, c):
        self.time, self.open, self.high, self.low, self.close = t, o, h, l, c
        self.volume = 100


class _HTFAdapter:
    """Serves crafted H4 + D1 candles for the agent's context fetch."""

    def __init__(self):
        h4 = FILLER + DEMAND_OB + [(104.2, 105.0, 103.5, 104.8)] * 20
        self.h4 = [_Candle(1_700_000_000 + k * 14400, *b) for k, b in enumerate(h4)]
        base = np.linspace(80, 104, 60)   # strongly rising D1 closes
        self.d1 = [_Candle(1_700_000_000 + k * 86400, x - 0.5, x + 0.5, x - 1.0, x)
                   for k, x in enumerate(base)]

    async def get_candles(self, symbol, timeframe="M5", count=200):
        return {"H4": self.h4, "D1": self.d1}[timeframe]


def _h1_bars_for_agent():
    o, h, l, c = _h1_long_setup()
    pad = 40
    bars = [{"time": 1_700_000_000 + k * 3600, "open": 103.0, "high": 103.5,
             "low": 102.5, "close": 103.0, "volume": 1} for k in range(pad)]
    for k in range(5):
        bars.append({"time": 1_700_000_000 + (pad + k) * 3600, "open": o[k],
                     "high": h[k], "low": l[k], "close": c[k], "volume": 1})
    return bars


@pytest.mark.asyncio
async def test_swing_agent_produces_contract_signal():
    from app.services.agent.swing_agent import SwingAgent

    config = dict(
        d1_ema_period=10, d1_slope_bars=0, displacement_atr_mult=1.5,
        ob_only=False, atr_period=3, min_sl_atr_mult=0.05, max_sl_atr_mult=5.0,
    )
    agent = SwingAgent(agent_id=1, symbol="US30", broker_name="oanda", config=config)
    agent._load_last_trade_time_from_db = lambda: 0.0

    sig = await agent.evaluate(_h1_bars_for_agent(), broker_adapter=_HTFAdapter(),
                               balance=10_000.0)
    assert sig is not None
    assert sig["direction"] == "BUY"
    assert sig["agent_type"] == "swing"
    assert sig["stop_loss"] < sig["entry_price"] < sig["take_profit"]
    assert sig["lot_size"] >= 1
    assert 0 < sig["confidence"] <= 0.85


@pytest.mark.asyncio
async def test_swing_agent_insufficient_bars_returns_none():
    from app.services.agent.swing_agent import SwingAgent
    agent = SwingAgent(agent_id=1, symbol="US30", broker_name="oanda", config={})
    agent._load_last_trade_time_from_db = lambda: 0.0
    assert await agent.evaluate([{"time": 0, "open": 1, "high": 1, "low": 1,
                                  "close": 1, "volume": 1}] * 5) is None


@pytest.mark.asyncio
async def test_swing_agent_daily_trade_limit_blocks():
    from app.services.agent.swing_agent import SwingAgent
    agent = SwingAgent(agent_id=1, symbol="US30", broker_name="oanda",
                       config={"max_trades_per_day": 1})
    agent._load_last_trade_time_from_db = lambda: 0.0
    sig = await agent.evaluate(_h1_bars_for_agent(), broker_adapter=_HTFAdapter(),
                               balance=10_000.0, daily_trade_count=1)
    assert sig is None


def test_swing_agent_declares_swing_defaults():
    from app.services.agent.swing_agent import SwingAgent
    agent = SwingAgent(agent_id=1, symbol="XAUUSD", broker_name="oanda", config={})
    assert agent.primary_timeframe == "H1"
    assert agent.default_max_hold_hours == 240
    assert agent.load() is True


# ── Engine dispatch ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_engine_dispatch_starts_swing_agent(db_session, test_user):
    from tests.conftest import TestingSessionLocal
    from app.models.agent import TradingAgent
    from app.services.agent.engine import AgentRunner
    from app.services.agent.swing_agent import SwingAgent

    record = TradingAgent(
        created_by=test_user.id, name="swing-test", symbol="XAUUSD",
        agent_type="swing", broker_name="oanda", mode="paper",
        status="running", risk_config={},
    )
    db_session.add(record)
    db_session.commit()

    with patch("app.services.agent.engine.SessionLocal", TestingSessionLocal):
        runner = AgentRunner(record.id)
        await runner.start()
        try:
            assert isinstance(runner._agent, SwingAgent)
        finally:
            await runner.stop()
