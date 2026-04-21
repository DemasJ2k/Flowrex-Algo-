"use client";

import { useState, useEffect } from "react";
import Modal from "@/components/ui/Modal";
import api from "@/lib/api";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";

const STEPS = ["Setup", "Risk & Mode", "Review"];

// Legacy "scalping" + "flowrex" types removed 2026-04-21 — they used the
// deprecated FlowrexAgent runtime that lacks today's filters / risk
// manager and kept showing up as options despite being unmaintained.
const AGENT_TYPES = [
  { value: "flowrex_v2", label: "Flowrex v2", desc: "120 features, 3-model ensemble (XGB+LGB+CatBoost), 4-layer MTF.", pipelineKey: "flowrex", color: "#f59e0b" },
  { value: "potential", label: "Potential Agent", desc: "85 institutional features (VWAP, ADX, ORB, anchored VWAPs). Walk-forward trained.", pipelineKey: "potential", color: "#22c55e" },
];

// Per-symbol model metadata from /api/ml/symbols (fetched on open).
interface SymbolInfo {
  symbol: string;
  asset_class: string;
  models: Array<{ pipeline: string; grade: string; model_type: string }>;
}

const BROKERS: Array<{ value: string; label: string }> = [
  { value: "oanda", label: "Oanda" },
  { value: "tradovate", label: "Tradovate" },
  { value: "ctrader", label: "cTrader" },
  { value: "mt5", label: "MT5" },
  { value: "interactive_brokers", label: "Interactive Brokers" },
];

const GRADE_ORDER = ["A", "B", "C", "D", "F"];
function bestGradeForPipeline(s: SymbolInfo, pipelineKey: string): string {
  const candidates = s.models
    .filter((m) => m.pipeline === pipelineKey)
    .map((m) => m.grade);
  for (const g of GRADE_ORDER) {
    if (candidates.includes(g)) return g;
  }
  return "—"; // no model for that pipeline
}
function gradeColor(grade: string): string {
  if (grade === "A") return "#22c55e";
  if (grade === "B") return "#3b82f6";
  if (grade === "C") return "#f59e0b";
  if (grade === "D") return "#f97316";
  if (grade === "F") return "#ef4444";
  return "#71717a";
}

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
  const [sizingMode, setSizingMode] = useState<"risk_pct" | "max_lots">("risk_pct");
  const [riskPerTrade, setRiskPerTrade] = useState(FALLBACK_RISK);
  const [maxLotSize, setMaxLotSize] = useState(5);
  const [maxDailyLoss, setMaxDailyLoss] = useState(FALLBACK_LOSS);
  const [cooldownBars, setCooldownBars] = useState(FALLBACK_COOLDOWN);
  const [agentType, setAgentType] = useState("flowrex_v2");
  const [mode, setMode] = useState("paper");
  const [sessionFilter, setSessionFilter] = useState(true);
  const [regimeFilter, setRegimeFilter] = useState(true);
  const [newsFilter, setNewsFilter] = useState(true);
  const [propFirmEnabled, setPropFirmEnabled] = useState(false);
  const [allowBuy, setAllowBuy] = useState(true);
  const [allowSell, setAllowSell] = useState(true);
  const [allowedSessions, setAllowedSessions] = useState<string[]>([
    "london", "ny_open", "ny_close",
  ]);
  const [loading, setLoading] = useState(false);
  // Dynamic per-symbol model metadata. Populated from /api/ml/symbols so the
  // wizard can show the deployed grade for each symbol under the selected
  // pipeline (e.g. "BTCUSD Grade A" for potential, "NAS100 Grade F" showing
  // the user that NAS100's current model is regime-broken).
  const [symbolInfo, setSymbolInfo] = useState<SymbolInfo[]>([]);

  // Fetch user's trading defaults from settings when wizard opens
  useEffect(() => {
    if (!open) return;
    api.get("/api/settings/").then((r) => {
      const sj = r.data?.settings_json || {};
      const t = sj.trading || {};
      if (t.risk_per_trade) setRiskPerTrade(t.risk_per_trade);
      if (t.max_daily_loss_pct) setMaxDailyLoss(t.max_daily_loss_pct * 100);
      if (t.cooldown_bars !== undefined) setCooldownBars(t.cooldown_bars);
      // Default filter toggles — load user's saved preferences
      if (t.news_filter_enabled !== undefined) setNewsFilter(t.news_filter_enabled);
      if (t.session_filter !== undefined) setSessionFilter(t.session_filter);
      if (t.regime_filter !== undefined) setRegimeFilter(t.regime_filter);
      if (r.data?.default_broker) setBroker(r.data.default_broker);
    }).catch(() => {}); // silently ignore — wizard still works with fallback defaults

    // Pull the symbol→models map once per open. Shows empty grid while
    // loading; falls back to a static safety list if the endpoint errors.
    api.get("/api/ml/symbols")
      .then((r) => setSymbolInfo(r.data || []))
      .catch(() => setSymbolInfo([]));
  }, [open]);

  const reset = () => {
    setStep(0); setSymbol("XAUUSD"); setCustomName(""); setBroker(FALLBACK_BROKER);
    setTimeframe("M5"); setAgentType("potential"); setSizingMode("risk_pct");
    setRiskPerTrade(FALLBACK_RISK); setMaxLotSize(5);
    setMaxDailyLoss(FALLBACK_LOSS); setCooldownBars(FALLBACK_COOLDOWN); setMode("paper");
    setSessionFilter(true); setRegimeFilter(true); setNewsFilter(true);
    setPropFirmEnabled(false); setAllowBuy(true); setAllowSell(true);
    setAllowedSessions(["london", "ny_open", "ny_close"]);
  };

  const agentName = customName || `${symbol} Flowrex`;

  const handleDeploy = async () => {
    setLoading(true);
    try {
      await api.post("/api/agents/", {
        name: agentName,
        symbol,
        timeframe,
        agent_type: agentType,
        broker_name: broker,
        mode,
        risk_config: {
          sizing_mode: sizingMode,
          risk_per_trade: riskPerTrade,
          max_lot_size: maxLotSize,
          max_daily_loss_pct: maxDailyLoss / 100,
          cooldown_bars: cooldownBars,
          session_filter: sessionFilter,
          regime_filter: regimeFilter,
          news_filter_enabled: newsFilter,
          prop_firm_enabled: propFirmEnabled,
          allow_buy: allowBuy,
          allow_sell: allowSell,
          allowed_sessions: sessionFilter ? allowedSessions : [],
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

      {/* Step 1: Setup — Symbol + Agent Type + Broker */}
      {step === 0 && (
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Agent Name</label>
            <input value={customName} onChange={(e) => setCustomName(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} placeholder={`${symbol} Flowrex`} />
          </div>
          {(() => {
            // Pick which pipeline grade to show next to each symbol based on
            // the currently-selected agent type. Falls back gracefully if the
            // symbols endpoint hasn't loaded yet (shows symbols without grades).
            const activePipelineKey = AGENT_TYPES.find((a) => a.value === agentType)?.pipelineKey || "potential";
            const FALLBACK_SYMBOLS = ["XAUUSD", "BTCUSD", "US30", "ES", "NAS100"];
            const symbolsToShow = symbolInfo.length > 0
              ? symbolInfo.map((s) => s.symbol)
              : FALLBACK_SYMBOLS;
            return (
              <div>
                <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>
                  Symbol
                  <span className="ml-2 text-[10px]" style={{ opacity: 0.6 }}>
                    grade shown for {AGENT_TYPES.find((a) => a.value === agentType)?.label}
                  </span>
                </label>
                <div className="grid grid-cols-2 gap-2">
                  {symbolsToShow.map((s) => {
                    const info = symbolInfo.find((x) => x.symbol === s);
                    const g = info ? bestGradeForPipeline(info, activePipelineKey) : "—";
                    const active = symbol === s;
                    return (
                      <button
                        key={s}
                        onClick={() => setSymbol(s)}
                        className={"flex items-center justify-between px-3 py-2 text-sm rounded-lg border transition-colors " + (active ? "border-blue-500 bg-blue-500/10 text-blue-400" : "hover:bg-white/5")}
                        style={{ borderColor: active ? undefined : "var(--border)" }}
                      >
                        <span className="font-medium">{s}</span>
                        <span
                          className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                          style={{ background: `${gradeColor(g)}20`, color: gradeColor(g) }}
                          title={g === "—" ? `No ${activePipelineKey} model deployed` : `Grade ${g}`}
                        >
                          {g === "—" ? "—" : `Grade ${g}`}
                        </span>
                      </button>
                    );
                  })}
                </div>
                {symbolInfo.length > 0 && (() => {
                  const sel = symbolInfo.find((x) => x.symbol === symbol);
                  const g = sel ? bestGradeForPipeline(sel, activePipelineKey) : "—";
                  if (g === "F") {
                    return (
                      <p className="text-[11px] mt-1.5 text-red-400 flex items-center gap-1">
                        <AlertTriangle size={12} />
                        {symbol} {activePipelineKey} is Grade F — model regime-broken, expect losses live.
                      </p>
                    );
                  }
                  if (g === "—") {
                    return (
                      <p className="text-[11px] mt-1.5 text-amber-400">
                        No {activePipelineKey} model deployed for {symbol} — retrain before enabling.
                      </p>
                    );
                  }
                  return null;
                })()}
              </div>
            );
          })()}
          <div>
            <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Agent Strategy</label>
            {AGENT_TYPES.map((a) => {
              const info = symbolInfo.find((x) => x.symbol === symbol);
              const grade = info ? bestGradeForPipeline(info, a.pipelineKey) : "?";
              return (
                <button
                  key={a.value}
                  onClick={() => setAgentType(a.value)}
                  className={`w-full text-left p-2.5 rounded-lg border transition-colors mb-2 ${agentType === a.value ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
                  style={{ borderColor: agentType === a.value ? undefined : "var(--border)" }}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-sm">{a.label}</span>
                    <span
                      className="text-xs px-1.5 py-0.5 rounded font-bold"
                      style={{ background: `${gradeColor(grade)}20`, color: gradeColor(grade) }}
                    >
                      {grade === "—" ? "No model" : `Grade ${grade}`}
                    </span>
                  </div>
                  <span className="text-xs" style={{ color: "var(--muted)" }}>{a.desc}</span>
                </button>
              );
            })}
          </div>
          <div>
            <label htmlFor="aw-broker" className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Broker</label>
            <select id="aw-broker" value={broker} onChange={(e) => setBroker(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
              {BROKERS.map((b) => (
                <option key={b.value} value={b.value}>{b.label}</option>
              ))}
            </select>
            {/* Timeframe dropdown removed (audit H18): all models are M5-only and
                the engine hardcodes get_candles(symbol, "M5", 500). The dropdown
                was vestigial — the field was sent to the API but ignored. */}
          </div>
        </div>
      )}

      {/* Step 2: Risk & Mode */}
      {step === 1 && (
        <div className="space-y-4">
          {/* Sizing Mode Toggle */}
          <div>
            <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Position Sizing Mode</label>
            <div className="grid grid-cols-2 gap-2">
              <button onClick={() => setSizingMode("risk_pct")}
                className={`p-2.5 text-center rounded-lg border transition-colors ${sizingMode === "risk_pct" ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
                style={{ borderColor: sizingMode === "risk_pct" ? undefined : "var(--border)" }}>
                <p className="font-medium text-sm">Risk % of Balance</p>
                <p className="text-xs" style={{ color: "var(--muted)" }}>Size based on risk tolerance</p>
              </button>
              <button onClick={() => setSizingMode("max_lots")}
                className={`p-2.5 text-center rounded-lg border transition-colors ${sizingMode === "max_lots" ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
                style={{ borderColor: sizingMode === "max_lots" ? undefined : "var(--border)" }}>
                <p className="font-medium text-sm">Max Lot Size</p>
                <p className="text-xs" style={{ color: "var(--muted)" }}>Cap lots, scale by confidence</p>
              </button>
            </div>
          </div>

          {/* Risk % Mode */}
          {sizingMode === "risk_pct" && (
            <div>
              <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Risk per trade</label>
              <div className="grid grid-cols-3 gap-2 mb-3">
                {RISK_PRESETS.map((r) => (
                  <button key={r.value} onClick={() => setRiskPerTrade(r.value)}
                    className={`p-2 text-center rounded-lg border transition-colors ${riskPerTrade === r.value ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
                    style={{ borderColor: riskPerTrade === r.value ? undefined : "var(--border)" }}>
                    <span className="font-medium text-sm block">{r.label}</span>
                    <span className="text-xs" style={{ color: "var(--muted)" }}>{r.desc}</span>
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-3">
                <input type="range" min="0.01" max="3" step="0.01"
                  value={riskPerTrade * 100}
                  onChange={(e) => setRiskPerTrade(parseFloat(e.target.value) / 100)}
                  className="flex-1 h-1.5 rounded-full appearance-none cursor-pointer"
                  style={{ background: `linear-gradient(to right, #8b5cf6 ${(riskPerTrade * 100 / 3) * 100}%, var(--border) ${(riskPerTrade * 100 / 3) * 100}%)` }} />
                <div className="flex items-center gap-1">
                  <input type="number" min="0.01" max="3" step="0.01"
                    value={(riskPerTrade * 100).toFixed(2)}
                    onChange={(e) => { const v = parseFloat(e.target.value); if (!isNaN(v) && v >= 0.01 && v <= 3) setRiskPerTrade(v / 100); }}
                    className="w-16 px-2 py-1 text-sm text-center rounded-lg border bg-transparent outline-none focus:border-blue-500"
                    style={{ borderColor: "var(--border)" }} />
                  <span className="text-xs" style={{ color: "var(--muted)" }}>%</span>
                </div>
              </div>
            </div>
          )}

          {/* Max Lots Mode */}
          {sizingMode === "max_lots" && (
            <div>
              <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Maximum Lot Size</label>
              <p className="text-xs mb-3" style={{ color: "var(--muted)" }}>
                Agent scales lots by confidence: low confidence → fewer lots, high confidence → up to max.
              </p>
              <div className="flex items-center gap-3">
                <input type="range" min="1" max="100" step="1"
                  value={maxLotSize}
                  onChange={(e) => setMaxLotSize(parseInt(e.target.value))}
                  className="flex-1 h-1.5 rounded-full appearance-none cursor-pointer"
                  style={{ background: `linear-gradient(to right, #8b5cf6 ${maxLotSize}%, var(--border) ${maxLotSize}%)` }} />
                <div className="flex items-center gap-1">
                  <input type="number" min="1" max="100" step="1"
                    value={maxLotSize}
                    onChange={(e) => { const v = parseInt(e.target.value); if (!isNaN(v) && v >= 1 && v <= 100) setMaxLotSize(v); }}
                    className="w-16 px-2 py-1 text-sm text-center rounded-lg border bg-transparent outline-none focus:border-blue-500"
                    style={{ borderColor: "var(--border)" }} />
                  <span className="text-xs" style={{ color: "var(--muted)" }}>lots</span>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 mt-3 text-xs text-center" style={{ color: "var(--muted)" }}>
                <div className="p-1.5 rounded border" style={{ borderColor: "var(--border)" }}>Low conf (52%) → {Math.max(1, Math.round(maxLotSize * 0.2))} lots</div>
                <div className="p-1.5 rounded border" style={{ borderColor: "var(--border)" }}>Med conf (70%) → {Math.max(1, Math.round(maxLotSize * 0.5))} lots</div>
                <div className="p-1.5 rounded border" style={{ borderColor: "var(--border)" }}>High conf (90%+) → {maxLotSize} lots</div>
              </div>
            </div>
          )}
          <div>
            <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Trading Mode</label>
            <div className="grid grid-cols-2 gap-2">
              {[
                { value: "paper", title: "Paper", desc: "Simulated" },
                { value: "live", title: "Live", desc: "Real money" },
              ].map((m) => (
                <button key={m.value} onClick={() => setMode(m.value)}
                  className={`p-3 text-center rounded-lg border transition-colors ${mode === m.value ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
                  style={{ borderColor: mode === m.value ? undefined : "var(--border)" }}>
                  <p className="font-medium text-sm">{m.title}</p>
                  <p className="text-xs" style={{ color: "var(--muted)" }}>{m.desc}</p>
                </button>
              ))}
            </div>
            {mode === "live" && (
              <div className="flex items-center gap-2 p-2 mt-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-400 text-xs">
                <AlertTriangle size={16} />
                <span>Live trading uses real money.</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Step 3: Review */}
      {step === 2 && (
        <div className="space-y-2 text-sm">
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Name</span><span>{agentName}</span>
          </div>
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Symbol</span><span>{symbol} / {timeframe}</span>
          </div>
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Agent Type</span><span className="capitalize">{agentType}</span>
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
