"""
Swing rules v1 — the single source of truth for the H1/H4/D1 swing system.

Consumed by BOTH the live SwingAgent and scripts/backtest_swing.py so that
backtest and live behaviour cannot drift apart (the flowrex-era lesson).
Pure functions + a small incremental ZoneTracker; no broker, DB, or ML
dependencies.

THE RULE SET (transparent by design — every trade is explainable):

  1. D1 TREND GATE — close above EMA(50) → longs only; below → shorts only.
  2. H4 ZONES (supply/demand) —
     * Order block: the last opposite-coloured H4 candle immediately before
       a displacement (move ≥ 1.5×ATR(H4) within the next 2 bars that also
       clears the candle's far extreme). The OB candle's range is the zone.
     * Fair-value gap: 3-bar gap (bullish: low[i] > high[i-2]) — the gap is
       the zone.
     * A zone dies when an H4 bar CLOSES through its far edge, when it has
       been touched more than `max_touches` times, or when it ages out of
       the lookback window.
  3. H1 TRIGGER — within the last `touch_window` H1 bars price tagged a
     live zone that agrees with the D1 trend, and the current H1 bar
     confirms: closes beyond the previous H1 bar's extreme (micro-BOS) AND
     closes back on the far side of the zone edge (rejection).
  4. STOPS/TARGETS — SL beyond the zone's far edge + 0.25×ATR(H4) buffer;
     TP at a fixed `target_rr` multiple (default 2R). Entries with an SL
     distance below 0.25×ATR(H4) or above 2×ATR(H4) are skipped (zone too
     tight/too wide to be meaningful).
  5. HYGIENE — one trade per zone, cooldown between entries, one open
     position per symbol (enforced account-wide by the engine).
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.services.backtest.indicators import atr as compute_atr, ema as compute_ema

H1_SEC = 3600
H4_SEC = 14400
D1_SEC = 86400


@dataclass
class SwingConfig:
    """Defaults = variant F of the 2026-07-13 tuning protocol.

    Protocol (documented in scripts/backtest_swing.py + DEVLOG): six
    variants (A–F) evaluated on 2010–2021 ONLY; F (ob_only + d1 slope +
    2.0×ATR displacement + 3R target) won on pooled expectancy and was
    verified ONCE out-of-sample on 2022–2026: XAUUSD +0.30R/trade PF 1.45,
    US30 ≈ flat. Statistically unproven at 95% (n=124 OOS) — paper-trade
    a full quarter before funding anything.
    """
    # D1 trend gate
    d1_ema_period: int = 50
    d1_slope_bars: int = 5               # EMA must also slope with the trend
    # H4 zone construction
    zone_lookback_bars: int = 250        # zones older than this (H4 bars) age out
    displacement_atr_mult: float = 2.0   # min move (×ATR H4) to confirm an order block
    displacement_span: int = 2           # bars allowed for the displacement to play out
    max_touches: int = 1                 # zone is spent after this many touches
    ob_only: bool = True                 # bare FVG zones tested worse — order blocks only
    # H1 trigger
    touch_window_h1_bars: int = 3        # zone touch must be within the last N H1 bars
    # Stops / targets
    sl_buffer_atr_mult: float = 0.25     # SL buffer beyond zone edge (×ATR H4)
    min_sl_atr_mult: float = 0.25        # skip if SL distance below this (×ATR H4)
    max_sl_atr_mult: float = 2.0         # skip if SL distance above this (×ATR H4)
    target_rr: float = 3.0
    # Hygiene
    cooldown_h1_bars: int = 12
    atr_period: int = 14

    @classmethod
    def from_dict(cls, cfg: dict) -> "SwingConfig":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (cfg or {}).items() if k in valid})


@dataclass
class Zone:
    kind: str          # "demand" | "supply"
    source: str        # "ob" | "fvg"
    top: float
    bottom: float
    created_idx: int   # H4 index at which the zone became known (displacement/gap confirmed)
    touches: int = 0
    alive: bool = True
    consumed: bool = False   # a trade was taken off this zone
    _in_touch: bool = field(default=False, repr=False)  # price currently inside (dedupe touch counting)

    @property
    def far_edge(self) -> float:
        return self.bottom if self.kind == "demand" else self.top

    @property
    def near_edge(self) -> float:
        return self.top if self.kind == "demand" else self.bottom


class ZoneTracker:
    """Incremental H4 supply/demand zone book.

    Feed completed H4 bars in order via on_h4_bar(); read `zones` for the
    current book. Detection lags by `displacement_span` bars because an
    order block is only known once its displacement has played out —
    which is exactly the information a live trader would have.
    """

    def __init__(self, cfg: SwingConfig):
        self.cfg = cfg
        self.zones: list[Zone] = []
        self._o: list[float] = []
        self._h: list[float] = []
        self._l: list[float] = []
        self._c: list[float] = []
        self._atr: list[float] = []

    def on_h4_bar(self, o: float, h: float, l: float, c: float, atr_val: float):
        cfg = self.cfg
        self._o.append(o); self._h.append(h); self._l.append(l); self._c.append(c)
        self._atr.append(atr_val)
        i = len(self._c) - 1

        # 1. Update existing zones with this bar
        for z in self.zones:
            if not z.alive:
                continue
            if i - z.created_idx > cfg.zone_lookback_bars:
                z.alive = False
                continue
            if z.kind == "demand":
                if c < z.bottom:            # H4 close through far edge
                    z.alive = False
                    continue
                inside = l <= z.top
            else:
                if c > z.top:
                    z.alive = False
                    continue
                inside = h >= z.bottom
            if inside and not z._in_touch:
                z.touches += 1
                if z.touches > cfg.max_touches:
                    z.alive = False
            z._in_touch = inside

        # 2. Detect new zones whose confirmation completes at this bar
        self._detect_ob(i)
        self._detect_fvg(i)

    def _detect_ob(self, i: int):
        """Order block at candle j = i - displacement_span, confirmed by bar i."""
        cfg = self.cfg
        j = i - cfg.displacement_span
        if j < 0:
            return
        a = self._atr[j]
        if not np.isfinite(a) or a <= 0:
            return
        o, h, l, c = self._o[j], self._h[j], self._l[j], self._c[j]
        span_h = max(self._h[j + 1:i + 1])
        span_l = min(self._l[j + 1:i + 1])
        span_c = self._c[i]
        # Bearish candle + bullish displacement clearing its high → demand
        if c < o and span_c > h and (span_h - c) >= cfg.displacement_atr_mult * a:
            self._add_zone(Zone("demand", "ob", top=h, bottom=l, created_idx=i))
        # Bullish candle + bearish displacement clearing its low → supply
        if c > o and span_c < l and (c - span_l) >= cfg.displacement_atr_mult * a:
            self._add_zone(Zone("supply", "ob", top=h, bottom=l, created_idx=i))

    def _detect_fvg(self, i: int):
        if i < 2 or self.cfg.ob_only:
            return
        # Bullish FVG: gap between high[i-2] and low[i]
        if self._l[i] > self._h[i - 2]:
            self._add_zone(Zone("demand", "fvg", top=self._l[i], bottom=self._h[i - 2], created_idx=i))
        # Bearish FVG
        if self._h[i] < self._l[i - 2]:
            self._add_zone(Zone("supply", "fvg", top=self._l[i - 2], bottom=self._h[i], created_idx=i))

    def _add_zone(self, z: Zone):
        if z.top <= z.bottom:
            return
        # Skip near-duplicates (same kind, >70% overlap with a live zone)
        for existing in self.zones:
            if not existing.alive or existing.kind != z.kind:
                continue
            lo = max(existing.bottom, z.bottom)
            hi = min(existing.top, z.top)
            if hi > lo and (hi - lo) >= 0.7 * (z.top - z.bottom):
                return
        self.zones.append(z)

    def live_zones(self, kind: Optional[str] = None) -> list[Zone]:
        return [z for z in self.zones
                if z.alive and not z.consumed and (kind is None or z.kind == kind)]


def d1_trend_series(d1_closes: np.ndarray, ema_period: int = 50, slope_bars: int = 0) -> np.ndarray:
    """Per-bar D1 trend: +1 long-only, -1 short-only, 0 no-trade.

    +1 requires close above EMA (and, when slope_bars > 0, the EMA itself
    rising over the last slope_bars bars). Symmetric for -1. Used by BOTH
    the live agent (last value) and the backtest (whole series) so the
    trend gate cannot drift between them.
    """
    c = np.asarray(d1_closes, dtype=np.float64)
    if len(c) < ema_period + max(1, slope_bars):
        return np.zeros(len(c), dtype=np.int64)
    e = compute_ema(c, ema_period)
    up = c > e
    down = c < e
    if slope_bars > 0:
        e_prev = np.concatenate([np.full(slope_bars, np.nan), e[:-slope_bars]])
        with np.errstate(invalid="ignore"):
            up = up & (e > e_prev)
            down = down & (e < e_prev)
    return np.where(up, 1, np.where(down, -1, 0)).astype(np.int64)


def d1_trend(d1_closes: np.ndarray, ema_period: int = 50, slope_bars: int = 0) -> int:
    """Trend for the latest completed D1 bar (live-agent convenience)."""
    s = d1_trend_series(d1_closes, ema_period, slope_bars)
    return int(s[-1]) if len(s) else 0


def check_entry(
    h1_o: np.ndarray, h1_h: np.ndarray, h1_l: np.ndarray, h1_c: np.ndarray,
    i: int,
    tracker: ZoneTracker,
    trend: int,
    atr_h4_now: float,
    cfg: SwingConfig,
    last_entry_i: int = -10**9,
) -> Optional[dict]:
    """Evaluate the H1 trigger at bar index i. Returns a rule signal or None.

    The returned dict is broker-agnostic: direction ±1, reference entry
    (H1 close), stop, target, and the zone that produced it. Callers handle
    fills, sizing, and costs.
    """
    if i < 1 or trend == 0:
        return None
    if not np.isfinite(atr_h4_now) or atr_h4_now <= 0:
        return None
    if i - last_entry_i < cfg.cooldown_h1_bars:
        return None

    close = float(h1_c[i])
    w = max(1, cfg.touch_window_h1_bars)
    win_lo = float(np.min(h1_l[max(0, i - w + 1):i + 1]))
    win_hi = float(np.max(h1_h[max(0, i - w + 1):i + 1]))

    if trend > 0:
        micro_bos = close > float(h1_h[i - 1])
        if not micro_bos:
            return None
        candidates = [z for z in tracker.live_zones("demand")
                      if win_lo <= z.top and close > z.top and close > z.bottom]
        if not candidates:
            return None
        zone = max(candidates, key=lambda z: z.top)   # nearest demand below price
        sl = zone.bottom - cfg.sl_buffer_atr_mult * atr_h4_now
        sl_dist = close - sl
        direction = 1
    else:
        micro_bos = close < float(h1_l[i - 1])
        if not micro_bos:
            return None
        candidates = [z for z in tracker.live_zones("supply")
                      if win_hi >= z.bottom and close < z.bottom and close < z.top]
        if not candidates:
            return None
        zone = min(candidates, key=lambda z: z.bottom)  # nearest supply above price
        sl = zone.top + cfg.sl_buffer_atr_mult * atr_h4_now
        sl_dist = sl - close
        direction = -1

    if sl_dist < cfg.min_sl_atr_mult * atr_h4_now:
        return None
    if sl_dist > cfg.max_sl_atr_mult * atr_h4_now:
        return None

    tp = close + direction * cfg.target_rr * sl_dist

    confidence = 0.60
    if zone.touches <= 1:
        confidence += 0.10          # fresh zone
    if zone.source == "ob":
        confidence += 0.05          # order blocks over bare FVGs
    confidence = min(confidence, 0.85)

    zone.consumed = True
    return {
        "direction": direction,
        "entry_ref": close,
        "stop_loss": float(sl),
        "take_profit": float(tp),
        "sl_dist": float(sl_dist),
        "confidence": round(confidence, 2),
        "zone_kind": zone.kind,
        "zone_source": zone.source,
        "zone_top": zone.top,
        "zone_bottom": zone.bottom,
        "reason": f"swing_v1:{zone.kind}/{zone.source} trend={'up' if trend > 0 else 'down'}",
    }


def h4_atr_series(h4_h: np.ndarray, h4_l: np.ndarray, h4_c: np.ndarray, period: int = 14) -> np.ndarray:
    return compute_atr(np.asarray(h4_h, dtype=np.float64),
                       np.asarray(h4_l, dtype=np.float64),
                       np.asarray(h4_c, dtype=np.float64), period)


def completed_idx(times: np.ndarray, bar_seconds: int, now_ts: int) -> int:
    """Index of the last bar of `times` (open timestamps) that has fully
    closed as of wall-clock `now_ts`. -1 if none."""
    return int(np.searchsorted(np.asarray(times) + bar_seconds, now_ts, side="right")) - 1
