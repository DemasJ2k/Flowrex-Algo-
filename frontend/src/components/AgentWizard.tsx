"use client";

import { useState, useEffect } from "react";
import Modal from "@/components/ui/Modal";
import api from "@/lib/api";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";

const STEPS = ["Symbol", "Risk", "Filters", "Mode", "Review"];

const RISK_PRESETS = [
  { label: "Conservative", value: 0.0025, desc: "0.25% per trade" },
  { label: "Moderate", value: 0.005, desc: "0.5% per trade" },
  { label: "Aggressive", value: 0.01, desc: "1% per trade" },
];

const FALLBACK_RISK = 0.005;
const FALLBACK_LOSS = 4.0;
const FALLBACK_COOLDOWN = 3;
const FALLBACK_BROKER = "oanda";

export default function AgentWizard({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [step, setStep] = useState(0);
  const [symbol, setSymbol] = useState("XAUUSD");
  const [customName, setCustomName] = useState("");
  const [broker, setBroker] = useState(FALLBACK_BROKER);
  const [timeframe, setTimeframe] = useState("M5");
  const [riskPerTrade, setRiskPerTrade] = useState(FALLBACK_RISK);
  const [maxDailyLoss, setMaxDailyLoss] = useState(FALLBACK_LOSS);
  const [cooldownBars, setCooldownBars] = useState(FALLBACK_COOLDOWN);
  const [mode, setMode] = useState("paper");
  const [sessionFilter, setSessionFilter] = useState(true);
  const [regimeFilter, setRegimeFilter] = useState(true);
  const [newsFilter, setNewsFilter] = useState(true);
  const [loading, setLoading] = useState(false);

  // Fetch user's trading defaults from settings when wizard opens
  useEffect(() => {
    if (!open) return;
    api.get("/api/settings/").then((r) => {
      const sj = r.data?.settings_json || {};
      const t = sj.trading || {};
      if (t.risk_per_trade) setRiskPerTrade(t.risk_per_trade);
      if (t.max_daily_loss_pct) setMaxDailyLoss(t.max_daily_loss_pct * 100);
      if (t.cooldown_bars !== undefined) setCooldownBars(t.cooldown_bars);
      if (r.data?.default_broker) setBroker(r.data.default_broker);
    }).catch(() => {}); // silently ignore — wizard still works with fallback defaults
  }, [open]);

  const reset = () => {
    setStep(0); setSymbol("XAUUSD"); setCustomName(""); setBroker(FALLBACK_BROKER);
    setTimeframe("M5"); setRiskPerTrade(FALLBACK_RISK); setMaxDailyLoss(FALLBACK_LOSS);
    setCooldownBars(FALLBACK_COOLDOWN); setMode("paper"); setSessionFilter(true);
    setRegimeFilter(true); setNewsFilter(true);
  };

  const agentName = customName || `${symbol} Flowrex`;

  const handleDeploy = async () => {
    setLoading(true);
    try {
      await api.post("/api/agents/", {
        name: agentName,
        symbol,
        timeframe,
        agent_type: "flowrex",
        broker_name: broker,
        mode,
        risk_config: {
          risk_per_trade: riskPerTrade,
          max_daily_loss_pct: maxDailyLoss / 100,
          cooldown_bars: cooldownBars,
          session_filter: sessionFilter,
          regime_filter: regimeFilter,
          news_filter_enabled: newsFilter,
        },
      });
      toast.success(`Agent "${agentName}" created`);
      onCreated();
      onClose();
      reset();
    } catch (e) {
      toast.error(`Failed to create agent: ${getErrorMessage(e)}`);
    } finally { setLoading(false); }
  };

  return (
    <Modal open={open} onClose={onClose} title="Create Agent" width="max-w-md">
      {/* Step indicators */}
      <div className="flex items-center gap-1 mb-5">
        {STEPS.map((s, i) => (
          <div key={s} className="flex items-center gap-1">
            <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium ${
              i <= step ? "bg-blue-600 text-white" : "border"
            }`} style={i > step ? { borderColor: "var(--border)", color: "var(--muted)" } : {}}>
              {i + 1}
            </div>
            {i < STEPS.length - 1 && <div className="w-6 h-px" style={{ background: "var(--border)" }} />}
          </div>
        ))}
      </div>

      {/* Step 1: Symbol + Config */}
      {step === 0 && (
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Agent Name</label>
            <input value={customName} onChange={(e) => setCustomName(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} placeholder={`${symbol} Flowrex`} />
          </div>
          <div>
            <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Symbol</label>
            <div className="grid grid-cols-3 gap-2">
              {["XAUUSD", "BTCUSD", "US30", "ES", "NAS100", "EURUSD", "GBPUSD"].map((s) => (
                <button key={s} onClick={() => setSymbol(s)}
                  className={"px-3 py-2 text-sm rounded-lg border transition-colors " + (symbol === s ? "border-blue-500 bg-blue-500/10 text-blue-400" : "hover:bg-white/5")}
                  style={{ borderColor: symbol === s ? undefined : "var(--border)" }}>{s}</button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Broker</label>
              <select value={broker} onChange={(e) => setBroker(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                <option value="oanda">Oanda</option>
                <option value="ctrader">cTrader</option>
                <option value="mt5">MT5</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Timeframe</label>
              <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                {["M1", "M5", "M15", "H1", "H4", "D1"].map((tf) => <option key={tf} value={tf}>{tf}</option>)}
              </select>
            </div>
          </div>
        </div>
      )}

      {/* Step 2: Risk */}
      {step === 1 && (
        <div className="space-y-3">
          <p className="text-sm mb-3" style={{ color: "var(--muted)" }}>Risk per trade</p>
          {RISK_PRESETS.map((r) => (
            <button
              key={r.value}
              onClick={() => setRiskPerTrade(r.value)}
              className={`w-full text-left p-3 rounded-lg border transition-colors ${
                riskPerTrade === r.value ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"
              }`}
              style={{ borderColor: riskPerTrade === r.value ? undefined : "var(--border)" }}
            >
              <span className="font-medium text-sm">{r.label}</span>
              <span className="text-xs ml-2" style={{ color: "var(--muted)" }}>{r.desc}</span>
            </button>
          ))}
          <div className="pt-2">
            <label className="text-xs" style={{ color: "var(--muted)" }}>Custom Risk (%)</label>
            <input
              type="number"
              min="0.1" max="3" step="0.1"
              value={(riskPerTrade * 100).toFixed(1)}
              onChange={(e) => setRiskPerTrade(parseFloat(e.target.value) / 100)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500 mt-1"
              style={{ borderColor: "var(--border)" }}
            />
          </div>
          <div className="grid grid-cols-2 gap-3 pt-2">
            <div>
              <label className="text-xs" style={{ color: "var(--muted)" }}>Max Daily Loss (%)</label>
              <input type="number" min="1" max="10" step="0.5" value={maxDailyLoss.toFixed(1)}
                onChange={(e) => setMaxDailyLoss(parseFloat(e.target.value) || 4)}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500 mt-1"
                style={{ borderColor: "var(--border)" }} />
            </div>
            <div>
              <label className="text-xs" style={{ color: "var(--muted)" }}>Cooldown (bars)</label>
              <input type="number" min="1" max="20" value={cooldownBars}
                onChange={(e) => setCooldownBars(parseInt(e.target.value) || 3)}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500 mt-1"
                style={{ borderColor: "var(--border)" }} />
            </div>
          </div>
        </div>
      )}

      {/* Step 3: Filters */}
      {step === 2 && (
        <div className="space-y-4">
          <p className="text-sm mb-3" style={{ color: "var(--muted)" }}>Smart filters adapt your agent to market conditions</p>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={sessionFilter} onChange={(e) => setSessionFilter(e.target.checked)} className="rounded" />
            Session filter (reduce risk during Asian hours, skip dead zones)
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={regimeFilter} onChange={(e) => setRegimeFilter(e.target.checked)} className="rounded" />
            Regime filter (adjust risk by market regime: trending/ranging/volatile)
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={newsFilter} onChange={(e) => setNewsFilter(e.target.checked)} className="rounded" />
            News filter (skip trading during high-impact economic events)
          </label>
        </div>
      )}

      {/* Step 4: Mode */}
      {step === 3 && (
        <div className="space-y-3">
          <p className="text-sm mb-3" style={{ color: "var(--muted)" }}>Trading mode</p>
          {[
            { value: "paper", title: "Paper Trading", desc: "Simulated trades, no real money" },
            { value: "live", title: "Live Trading", desc: "Real trades with real money" },
          ].map((m) => (
            <button
              key={m.value}
              onClick={() => setMode(m.value)}
              className={`w-full text-left p-3 rounded-lg border transition-colors ${
                mode === m.value ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"
              }`}
              style={{ borderColor: mode === m.value ? undefined : "var(--border)" }}
            >
              <p className="font-medium text-sm">{m.title}</p>
              <p className="text-xs mt-0.5" style={{ color: "var(--muted)" }}>{m.desc}</p>
            </button>
          ))}
          {mode === "live" && (
            <div className="flex items-center gap-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-400 text-xs">
              <AlertTriangle size={16} />
              <span>Live trading uses real money. Make sure your risk settings are correct.</span>
            </div>
          )}
        </div>
      )}

      {/* Step 5: Review */}
      {step === 4 && (
        <div className="space-y-2 text-sm">
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Name</span><span>{agentName}</span>
          </div>
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Symbol</span><span>{symbol} / {timeframe}</span>
          </div>
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Broker</span><span>{broker}</span>
          </div>
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Risk</span><span>{(riskPerTrade * 100).toFixed(2)}% | Daily Max: {maxDailyLoss.toFixed(1)}% | Cooldown: {cooldownBars} bars</span>
          </div>
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Session Filter</span><span>{sessionFilter ? "On" : "Off"}</span>
          </div>
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Regime Filter</span><span>{regimeFilter ? "On" : "Off"}</span>
          </div>
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>News Filter</span><span>{newsFilter ? "On" : "Off"}</span>
          </div>
          <div className="flex justify-between py-1">
            <span style={{ color: "var(--muted)" }}>Mode</span><span>{mode}</span>
          </div>
        </div>
      )}

      {/* Navigation */}
      <div className="flex justify-between mt-5">
        <button
          onClick={() => step > 0 ? setStep(step - 1) : onClose()}
          className="px-4 py-2 text-sm rounded-lg border hover:bg-white/5 transition-colors"
          style={{ borderColor: "var(--border)" }}
        >
          {step === 0 ? "Cancel" : "Back"}
        </button>
        {step < STEPS.length - 1 ? (
          <button
            onClick={() => setStep(step + 1)}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition-colors"
          >
            Next
          </button>
        ) : (
          <button
            onClick={handleDeploy}
            disabled={loading}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50 transition-colors"
          >
            {loading ? "Deploying..." : "Deploy Agent"}
          </button>
        )}
      </div>
    </Modal>
  );
}
