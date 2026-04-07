"use client";

import { useState } from "react";
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
  const [riskPerTrade, setRiskPerTrade] = useState(((cfg.risk_per_trade as number) || 0.005) * 100);
  const [maxDailyLoss, setMaxDailyLoss] = useState(((cfg.max_daily_loss_pct as number) || 0.04) * 100);
  const [cooldown, setCooldown] = useState((cfg.cooldown_bars as number) || 3);
  const [mode, setMode] = useState(agent?.mode || "paper");
  const [sessionFilter, setSessionFilter] = useState(cfg.session_filter !== false);
  const [regimeFilter, setRegimeFilter] = useState(cfg.regime_filter !== false);
  const [newsFilter, setNewsFilter] = useState(cfg.news_filter_enabled !== false);
  const [loading, setLoading] = useState(false);

  // Reset state when agent changes
  if (agent && name !== agent.name && !loading) {
    setName(agent.name);
    setRiskPerTrade((agent.risk_config?.risk_per_trade || 0.005) * 100);
    setMaxDailyLoss((agent.risk_config?.max_daily_loss_pct || 0.04) * 100);
    setCooldown(agent.risk_config?.cooldown_bars || 3);
    setMode(agent.mode);
  }

  if (!agent) return null;

  const handleSave = async () => {
    setLoading(true);
    try {
      await api.put(`/api/agents/${agent.id}`, {
        name,
        mode,
        risk_config: {
          risk_per_trade: riskPerTrade / 100,
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

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Risk per Trade (%)</label>
            <input type="number" step="0.1" min="0.1" max="5" value={riskPerTrade.toFixed(1)}
              onChange={(e) => setRiskPerTrade(parseFloat(e.target.value) || 0.5)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} />
          </div>
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

        {agent.agent_type === "expert" && (
          <div className="space-y-2 pt-2 border-t" style={{ borderColor: "var(--border)" }}>
            <p className="text-xs font-medium" style={{ color: "var(--muted)" }}>Expert Filters</p>
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
        )}

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
