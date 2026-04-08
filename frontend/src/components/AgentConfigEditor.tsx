"use client";

import { useState, useEffect } from "react";
import Modal from "@/components/ui/Modal";
import api from "@/lib/api";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import type { Agent } from "@/types";

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
  const [loading, setLoading] = useState(false);

  // Reset state when agent changes
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
  }, [agent?.id]);

  if (!agent) return null;

  const handleSave = async () => {
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
    <Modal open={open} onClose={onClose} title={"Edit " + agent.name} width="max-w-md">
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
              <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Risk per Trade (%)</label>
              <input type="number" step="0.01" min="0.01" max="3" value={riskPerTrade.toFixed(2)}
                onChange={(e) => setRiskPerTrade(parseFloat(e.target.value) || 0.5)}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                style={{ borderColor: "var(--border)" }} />
            </div>
          ) : (
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Max Lot Size</label>
              <input type="number" step="1" min="1" max="100" value={maxLotSize}
                onChange={(e) => setMaxLotSize(parseInt(e.target.value) || 5)}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                style={{ borderColor: "var(--border)" }} />
            </div>
          )}
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Max Daily Loss (%)</label>
            <input type="number" step="0.5" min="1" max="10" value={maxDailyLoss.toFixed(1)}
              onChange={(e) => setMaxDailyLoss(parseFloat(e.target.value) || 4)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Cooldown (bars)</label>
            <input type="number" min="1" max="20" value={cooldown}
              onChange={(e) => setCooldown(parseInt(e.target.value) || 3)}
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

        <div className="space-y-2 pt-2 border-t" style={{ borderColor: "var(--border)" }}>
          <p className="text-xs font-medium" style={{ color: "var(--muted)" }}>Agent Filters</p>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={sessionFilter} onChange={(e) => setSessionFilter(e.target.checked)} className="rounded" />
            Session filter
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={regimeFilter} onChange={(e) => setRegimeFilter(e.target.checked)} className="rounded" />
            Regime filter
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={newsFilter} onChange={(e) => setNewsFilter(e.target.checked)} className="rounded" />
            News filter
          </label>
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
