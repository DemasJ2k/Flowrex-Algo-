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
            <div>
              <label htmlFor="cfg-risk-per-trade" className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Risk per Trade (%)</label>
              <input
                id="cfg-risk-per-trade"
                type="number" step="0.01" min="0.01" max="3" value={riskPerTrade.toFixed(2)}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  if (!isNaN(v) && v >= 0.01 && v <= 3) setRiskPerTrade(v);
                }}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                style={{ borderColor: "var(--border)" }} />
            </div>
          ) : (
            <div>
              <label htmlFor="cfg-max-lot" className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Max Lot Size</label>
              <input
                id="cfg-max-lot"
                type="number" step="1" min="1" max="100" value={maxLotSize}
                onChange={(e) => {
                  const v = parseInt(e.target.value);
                  if (!isNaN(v) && v >= 1 && v <= 100) setMaxLotSize(v);
                }}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                style={{ borderColor: "var(--border)" }} />
            </div>
          )}
          <div>
            <label htmlFor="cfg-daily-loss" className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Max Daily Loss (%)</label>
            <input
              id="cfg-daily-loss"
              type="number" step="0.5" min="1" max="10" value={maxDailyLoss.toFixed(1)}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                if (!isNaN(v) && v >= 1 && v <= 10) setMaxDailyLoss(v);
              }}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} />
          </div>
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
          </label>
        </div>

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
