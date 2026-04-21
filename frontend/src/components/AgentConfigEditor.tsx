"use client";

import { useState, useEffect } from "react";
import Modal from "@/components/ui/Modal";
import api from "@/lib/api";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import type { Agent } from "@/types";

const ALL_SESSIONS = [
  { id: "asian",    label: "Asian",    hours: "00-08 UTC" },
  { id: "london",   label: "London",   hours: "08-13 UTC" },
  { id: "ny_open",  label: "NY Open",  hours: "13-17 UTC" },
  { id: "ny_close", label: "NY Close", hours: "17-21 UTC" },
  { id: "off_hours",label: "Off Hours",hours: "21-24 UTC" },
];


/**
 * Slider + number input that stay synced. Uses a string-backed text field so
 * the user can type partial values like "0." or "0.1" without the controlled
 * input snapping the cursor back every keystroke (which the previous
 * `value={riskPerTrade.toFixed(2)}` approach did). Clamps to [min, max]
 * on commit (blur / Enter / slider change), not on every keystroke.
 */
function SliderField({
  id, label, value, onChange, min, max, step, decimals, hint,
}: {
  id: string;
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  decimals: number;
  hint?: string;
}) {
  const [text, setText] = useState(value.toFixed(decimals));
  useEffect(() => { setText(value.toFixed(decimals)); }, [value, decimals]);

  const commit = (raw: string) => {
    const v = parseFloat(raw);
    if (isNaN(v)) { setText(value.toFixed(decimals)); return; }
    const clamped = Math.max(min, Math.min(max, v));
    onChange(Number(clamped.toFixed(decimals)));
    setText(clamped.toFixed(decimals));
  };

  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label htmlFor={id} className="block text-xs font-medium" style={{ color: "var(--muted)" }}>{label}</label>
        {hint && <span className="text-[10px]" style={{ color: "var(--muted)" }}>{hint}</span>}
      </div>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="text"
          inputMode="decimal"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={(e) => commit(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { commit((e.target as HTMLInputElement).value); (e.target as HTMLInputElement).blur(); } }}
          className="w-24 px-2 py-1.5 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500 tabular-nums"
          style={{ borderColor: "var(--border)" }}
        />
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="flex-1 accent-blue-500"
          aria-label={label}
        />
      </div>
    </div>
  );
}

export default function AgentConfigEditor({
  agent,
  open,
  onClose,
  onSaved,
}: {
  agent: Agent | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const cfg = (agent?.risk_config || {}) as Record<string, unknown>;
  const [name, setName] = useState(agent?.name || "");
  const [sizingMode, setSizingMode] = useState((cfg.sizing_mode as string) || "risk_pct");
  const [riskPerTrade, setRiskPerTrade] = useState(((cfg.risk_per_trade as number) || 0.005) * 100);
  const [maxLotSize, setMaxLotSize] = useState((cfg.max_lot_size as number) || 5);
  const [maxDailyLoss, setMaxDailyLoss] = useState(((cfg.max_daily_loss_pct as number) || 0.04) * 100);
  const [cooldown, setCooldown] = useState((cfg.cooldown_bars as number) || 3);
  const [mode, setMode] = useState(agent?.mode || "paper");
  const [sessionFilter, setSessionFilter] = useState(cfg.session_filter !== false);
  const [regimeFilter, setRegimeFilter] = useState(cfg.regime_filter !== false);
  const [newsFilter, setNewsFilter] = useState(cfg.news_filter_enabled !== false);
  const [propFirmEnabled, setPropFirmEnabled] = useState(cfg.prop_firm_enabled === true);
  const [allowBuy, setAllowBuy] = useState(cfg.allow_buy !== false);
  const [allowSell, setAllowSell] = useState(cfg.allow_sell !== false);
  const [allowedSessions, setAllowedSessions] = useState<string[]>(
    (cfg.allowed_sessions as string[]) || ["london", "ny_open", "ny_close"]
  );
  // Regime filter sub-controls + correlation toggle (2026-04-21 parity patch)
  const [allowedRegimes, setAllowedRegimes] = useState<string[]>(
    (cfg.allowed_regimes as string[]) || ["trending_up", "trending_down", "ranging", "volatile"]
  );
  const [useCorrelations, setUseCorrelations] = useState<boolean>(
    cfg.use_correlations !== false
  );
  // Scout-only tuning knobs (2026-04-21)
  const [lookbackBars, setLookbackBars] = useState<number>(
    (cfg.lookback_bars as number) || 40
  );
  const [instantEntryConfidence, setInstantEntryConfidence] = useState<number>(
    (cfg.instant_entry_confidence as number) || 0.85
  );
  const [maxPendingBars, setMaxPendingBars] = useState<number>(
    (cfg.max_pending_bars as number) || 10
  );
  const [pullbackAtrFraction, setPullbackAtrFraction] = useState<number>(
    (cfg.pullback_atr_fraction as number) || 0.50
  );
  const [dedupeWindowBars, setDedupeWindowBars] = useState<number>(
    (cfg.dedupe_window_bars as number) || 20
  );
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!agent || loading) return;
    const c = (agent.risk_config || {}) as Record<string, unknown>;
    setName(agent.name);
    setSizingMode((c.sizing_mode as string) || "risk_pct");
    setRiskPerTrade(((c.risk_per_trade as number) || 0.005) * 100);
    setMaxLotSize((c.max_lot_size as number) || 5);
    setMaxDailyLoss(((c.max_daily_loss_pct as number) || 0.04) * 100);
    setCooldown((c.cooldown_bars as number) || 3);
    setMode(agent.mode);
    setSessionFilter(c.session_filter !== false);
    setRegimeFilter(c.regime_filter !== false);
    setNewsFilter(c.news_filter_enabled !== false);
    setPropFirmEnabled(c.prop_firm_enabled === true);
    setAllowBuy(c.allow_buy !== false);
    setAllowSell(c.allow_sell !== false);
    setAllowedSessions((c.allowed_sessions as string[]) || ["london", "ny_open", "ny_close"]);
    setAllowedRegimes((c.allowed_regimes as string[]) || ["trending_up", "trending_down", "ranging", "volatile"]);
    setUseCorrelations(c.use_correlations !== false);
    setLookbackBars((c.lookback_bars as number) || 40);
    setInstantEntryConfidence((c.instant_entry_confidence as number) || 0.85);
    setMaxPendingBars((c.max_pending_bars as number) || 10);
    setPullbackAtrFraction((c.pullback_atr_fraction as number) || 0.50);
    setDedupeWindowBars((c.dedupe_window_bars as number) || 20);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent]);

  if (!agent) return null;

  const toggleSession = (id: string) => {
    setAllowedSessions((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    );
  };

  const handleSave = async () => {
    if (!allowBuy && !allowSell) {
      toast.error("At least one direction (BUY or SELL) must be enabled");
      return;
    }
    if (sessionFilter && allowedSessions.length === 0) {
      toast.error("Session filter is ON but no sessions are selected — agent would never trade");
      return;
    }
    setLoading(true);
    try {
      await api.put(`/api/agents/${agent.id}`, {
        name,
        mode,
        risk_config: {
          sizing_mode: sizingMode,
          risk_per_trade: riskPerTrade / 100,
          max_lot_size: maxLotSize,
          max_daily_loss_pct: maxDailyLoss / 100,
          cooldown_bars: cooldown,
          session_filter: sessionFilter,
          regime_filter: regimeFilter,
          news_filter_enabled: newsFilter,
          prop_firm_enabled: propFirmEnabled,
          allow_buy: allowBuy,
          allow_sell: allowSell,
          allowed_sessions: sessionFilter ? allowedSessions : [],
          allowed_regimes: regimeFilter ? allowedRegimes : [
            "trending_up", "trending_down", "ranging", "volatile",
          ],
          use_correlations: useCorrelations,
          ...(agent.agent_type === "scout" ? {
            lookback_bars: lookbackBars,
            instant_entry_confidence: instantEntryConfidence,
            max_pending_bars: maxPendingBars,
            pullback_atr_fraction: pullbackAtrFraction,
            dedupe_window_bars: dedupeWindowBars,
          } : {}),
        },
      });
      toast.success("Agent configuration updated");
      onSaved();
      onClose();
    } catch (e) {
      toast.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={"Edit " + agent.name} width="max-w-lg">
      <div className="space-y-4">
        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Agent Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)}
            className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
            style={{ borderColor: "var(--border)" }} />
        </div>

        {/* Position Sizing Mode */}
        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Position Sizing</label>
          <div className="grid grid-cols-2 gap-2">
            <button type="button" onClick={() => setSizingMode("risk_pct")}
              className={`p-2 text-center rounded-lg border text-xs transition-colors ${sizingMode === "risk_pct" ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: sizingMode === "risk_pct" ? undefined : "var(--border)" }}>
              Risk % of Balance
            </button>
            <button type="button" onClick={() => setSizingMode("max_lots")}
              className={`p-2 text-center rounded-lg border text-xs transition-colors ${sizingMode === "max_lots" ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: sizingMode === "max_lots" ? undefined : "var(--border)" }}>
              Max Lot Size
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          {sizingMode === "risk_pct" ? (
            <SliderField
              id="cfg-risk-per-trade"
              label="Risk per Trade (%)"
              value={riskPerTrade}
              onChange={setRiskPerTrade}
              min={0.01}
              max={3}
              step={0.01}
              decimals={2}
              hint="0.01 % – 3 %"
            />
          ) : (
            <SliderField
              id="cfg-max-lot"
              label="Max Lot Size"
              value={maxLotSize}
              onChange={setMaxLotSize}
              min={0.01}
              max={100}
              step={0.01}
              decimals={2}
              hint="0.01 – 100 lots"
            />
          )}
          <SliderField
            id="cfg-daily-loss"
            label="Max Daily Loss (%)"
            value={maxDailyLoss}
            onChange={setMaxDailyLoss}
            min={0.5}
            max={10}
            step={0.1}
            decimals={1}
            hint="0.5 % – 10 %"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label htmlFor="cfg-cooldown" className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Cooldown (bars)</label>
            <input
              id="cfg-cooldown"
              type="number" min="1" max="20" value={cooldown}
              onChange={(e) => {
                const v = parseInt(e.target.value);
                if (!isNaN(v) && v >= 1 && v <= 20) setCooldown(v);
              }}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Mode</label>
            <select value={mode} onChange={(e) => setMode(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none"
              style={{ borderColor: "var(--border)", background: "var(--card)" }}>
              <option value="paper">Paper</option>
              <option value="live">Live</option>
            </select>
          </div>
        </div>

        {/* Trade Direction Gate */}
        <div className="pt-3 border-t" style={{ borderColor: "var(--border)" }}>
          <p className="text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Allowed Trade Directions</p>
          <p className="text-[10px] mb-2" style={{ color: "var(--muted)" }}>
            Disable one to trade only longs or only shorts. Useful if analytics show a strong directional bias.
          </p>
          <div className="grid grid-cols-2 gap-2">
            <button type="button" onClick={() => setAllowBuy(!allowBuy)}
              className={`p-2 text-center rounded-lg border text-sm font-medium transition-colors ${allowBuy ? "border-emerald-500 bg-emerald-500/10 text-emerald-400" : "hover:bg-white/5 opacity-50"}`}
              style={{ borderColor: allowBuy ? undefined : "var(--border)" }}>
              {allowBuy ? "✓ " : ""}BUY (Longs)
            </button>
            <button type="button" onClick={() => setAllowSell(!allowSell)}
              className={`p-2 text-center rounded-lg border text-sm font-medium transition-colors ${allowSell ? "border-red-500 bg-red-500/10 text-red-400" : "hover:bg-white/5 opacity-50"}`}
              style={{ borderColor: allowSell ? undefined : "var(--border)" }}>
              {allowSell ? "✓ " : ""}SELL (Shorts)
            </button>
          </div>
        </div>

        {/* Session Selection */}
        <div className="pt-3 border-t" style={{ borderColor: "var(--border)" }}>
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-medium" style={{ color: "var(--muted)" }}>Trading Sessions</p>
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input type="checkbox" checked={sessionFilter} onChange={(e) => setSessionFilter(e.target.checked)} className="rounded" />
              Restrict to selected sessions
            </label>
          </div>
          <p className="text-[10px] mb-2" style={{ color: "var(--muted)" }}>
            {sessionFilter
              ? "Agent only trades during checked sessions. Uncheck sessions with poor WR in Analytics."
              : "All sessions allowed (filter disabled — enable checkbox to restrict)."}
          </p>
          <div className={`grid grid-cols-1 gap-1.5 ${!sessionFilter ? "opacity-40 pointer-events-none" : ""}`}>
            {ALL_SESSIONS.map((s) => {
              const checked = allowedSessions.includes(s.id);
              return (
                <label key={s.id} className={`flex items-center justify-between px-3 py-2 rounded-lg border cursor-pointer text-xs hover:bg-white/5 ${checked ? "border-blue-500 bg-blue-500/5" : ""}`}
                  style={{ borderColor: checked ? undefined : "var(--border)" }}>
                  <span className="flex items-center gap-2">
                    <input type="checkbox" checked={checked} onChange={() => toggleSession(s.id)} className="rounded" />
                    <span className="font-medium">{s.label}</span>
                  </span>
                  <span style={{ color: "var(--muted)" }}>{s.hours}</span>
                </label>
              );
            })}
          </div>
        </div>

        {/* Agent Filters */}
        <div className="space-y-2 pt-3 border-t" style={{ borderColor: "var(--border)" }}>
          <p className="text-xs font-medium" style={{ color: "var(--muted)" }}>Other Filters</p>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={newsFilter} onChange={(e) => setNewsFilter(e.target.checked)} className="rounded" />
            News filter <span className="text-[10px]" style={{ color: "var(--muted)" }}>(skip 30min before high-impact events)</span>
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={regimeFilter} onChange={(e) => setRegimeFilter(e.target.checked)} className="rounded" />
            Regime filter
            <span className="text-[10px]" style={{ color: "var(--muted)" }}>
              (skip trades outside allowed market states)
            </span>
          </label>
          {regimeFilter && (
            <div className="ml-6 grid grid-cols-2 gap-x-3 gap-y-1 p-2 rounded border" style={{ borderColor: "var(--border)" }}>
              {[
                { id: "trending_up",   label: "Trending up",   desc: "Strong upward slope" },
                { id: "trending_down", label: "Trending down", desc: "Strong downward slope" },
                { id: "ranging",       label: "Ranging",        desc: "Chop (ADX < 20)" },
                { id: "volatile",      label: "Volatile",       desc: "ATR > 75th pctile" },
              ].map((r) => (
                <label key={r.id} className="flex items-start gap-2 text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    checked={allowedRegimes.includes(r.id)}
                    onChange={(e) => {
                      if (e.target.checked) setAllowedRegimes([...allowedRegimes, r.id]);
                      else setAllowedRegimes(allowedRegimes.filter((x) => x !== r.id));
                    }}
                    className="rounded mt-0.5"
                  />
                  <span>
                    <span className="font-medium">{r.label}</span>
                    <span className="block text-[10px]" style={{ color: "var(--muted)" }}>{r.desc}</span>
                  </span>
                </label>
              ))}
            </div>
          )}
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={useCorrelations} onChange={(e) => setUseCorrelations(e.target.checked)} className="rounded" />
            Symbol correlations
            <span className="text-[10px]" style={{ color: "var(--muted)" }}>
              (include cross-symbol features in the model input)
            </span>
          </label>
        </div>

        {/* Scout tuning (only for scout agents) */}
        {agent.agent_type === "scout" && (
          <details className="pt-3 border-t group" style={{ borderColor: "var(--border)" }}>
            <summary className="cursor-pointer text-xs font-medium flex items-center justify-between" style={{ color: "var(--muted)" }}>
              <span>Scout tuning — lookback entry state machine</span>
              <span className="text-[10px] group-open:hidden">click to expand</span>
            </summary>
            <p className="text-[10px] mt-2 mb-3" style={{ color: "var(--muted)" }}>
              Scout stashes each signal as pending and waits for a pullback, break-of-structure,
              or high-confidence trigger before entering. These knobs tune that behaviour.
            </p>
            <div className="space-y-3">
              <SliderField
                id="cfg-lookback-bars"
                label="Lookback window (bars)"
                value={lookbackBars}
                onChange={(v) => setLookbackBars(Math.round(v))}
                min={10}
                max={120}
                step={1}
                decimals={0}
                hint="bars scanned for BOS reference"
              />
              <SliderField
                id="cfg-instant-conf"
                label="Instant-entry confidence"
                value={instantEntryConfidence}
                onChange={setInstantEntryConfidence}
                min={0.50}
                max={0.99}
                step={0.01}
                decimals={2}
                hint="skip wait if conf ≥ this"
              />
              <SliderField
                id="cfg-max-pending"
                label="Max pending bars"
                value={maxPendingBars}
                onChange={(v) => setMaxPendingBars(Math.round(v))}
                min={2}
                max={60}
                step={1}
                decimals={0}
                hint="discard pending after N bars"
              />
              <SliderField
                id="cfg-pullback-atr"
                label="Pullback distance (× ATR)"
                value={pullbackAtrFraction}
                onChange={setPullbackAtrFraction}
                min={0.10}
                max={2.00}
                step={0.05}
                decimals={2}
                hint="price must retrace this much"
              />
              <SliderField
                id="cfg-dedupe-window"
                label="Dedupe window (bars)"
                value={dedupeWindowBars}
                onChange={(v) => setDedupeWindowBars(Math.round(v))}
                min={0}
                max={100}
                step={1}
                decimals={0}
                hint="skip same-direction repeats"
              />
            </div>
          </details>
        )}

        {/* Prop Firm Mode */}
        <div className="pt-3 border-t" style={{ borderColor: "var(--border)" }}>
          <label className="flex items-center justify-between cursor-pointer">
            <div>
              <p className="text-sm font-medium">Prop Firm Mode</p>
              <p className="text-[10px]" style={{ color: "var(--muted)" }}>
                FTMO-style tiered DD: yellow −1.5% (size↓), red −2.5% (pause), hard −3% (close all).
              </p>
            </div>
            <input type="checkbox" checked={propFirmEnabled} onChange={(e) => setPropFirmEnabled(e.target.checked)} className="rounded" />
          </label>
          {propFirmEnabled && (
            <div className="mt-2 p-2 rounded-lg text-[11px]" style={{ background: "rgba(139,92,246,0.1)", border: "1px solid rgba(139,92,246,0.3)", color: "#c4b5fd" }}>
              Prop Firm Mode applies FTMO-grade risk limits. Intended for funded/challenge accounts.
              RiskManager tiered drawdown gates every trade.
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>Cancel</button>
          <button onClick={handleSave} disabled={loading}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">
            {loading ? "Saving..." : "Save Changes"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
