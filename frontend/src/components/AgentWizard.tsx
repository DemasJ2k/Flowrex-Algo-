"use client";

import { useState, useEffect } from "react";
import Modal from "@/components/ui/Modal";
import api from "@/lib/api";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";

const STEPS = ["Setup", "Risk & Mode", "Filters", "Review"];

const ALL_SESSIONS = [
  { id: "asian",    label: "Asian",    hours: "00–08 UTC" },
  { id: "london",   label: "London",   hours: "08–13 UTC" },
  { id: "ny_open",  label: "NY Open",  hours: "13–17 UTC" },
  { id: "ny_close", label: "NY Close", hours: "17–21 UTC" },
  { id: "off_hours",label: "Off Hours",hours: "21–24 UTC" },
];

const ALL_REGIMES = [
  { id: "trending_up",   label: "Trending up",   desc: "Strong upward slope" },
  { id: "trending_down", label: "Trending down", desc: "Strong downward slope" },
  { id: "ranging",       label: "Ranging",       desc: "Chop (ADX < 20)" },
  { id: "volatile",      label: "Volatile",      desc: "ATR > 75th pctile" },
];

// Legacy "scalping" + "flowrex" types removed 2026-04-21 — they used the
// deprecated FlowrexAgent runtime that lacks today's filters / risk
// manager and kept showing up as options despite being unmaintained.
const AGENT_TYPES = [
  { value: "flowrex_v2", label: "Flowrex v2", desc: "120 features, 3-model ensemble (XGB+LGB+CatBoost), 4-layer MTF.", pipelineKey: "flowrex", color: "#f59e0b" },
  { value: "potential", label: "Potential Agent", desc: "85 institutional features (VWAP, ADX, ORB, anchored VWAPs). Walk-forward trained.", pipelineKey: "potential", color: "#22c55e" },
  { value: "scout", label: "Scout Agent", desc: "Potential + 40-bar lookback. Waits for pullback / break-of-structure before entering (or instant entry if confidence ≥ 0.85).", pipelineKey: "potential", color: "#8b5cf6" },
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
  const [allowedRegimes, setAllowedRegimes] = useState<string[]>([
    "trending_up", "trending_down", "ranging", "volatile",
  ]);
  const [useCorrelations, setUseCorrelations] = useState(true);
  // Scout-only state machine knobs (mirror AgentConfigEditor defaults)
  const [lookbackBars, setLookbackBars] = useState(40);
  const [instantEntryConfidence, setInstantEntryConfidence] = useState(0.85);
  const [maxPendingBars, setMaxPendingBars] = useState(10);
  const [pullbackAtrFraction, setPullbackAtrFraction] = useState(0.50);
  const [dedupeWindowBars, setDedupeWindowBars] = useState(20);
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
      if (t.use_correlations !== undefined) setUseCorrelations(t.use_correlations);
      if (Array.isArray(t.allowed_regimes) && t.allowed_regimes.length > 0) {
        setAllowedRegimes(t.allowed_regimes);
      }
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
    setAllowedRegimes(["trending_up", "trending_down", "ranging", "volatile"]);
    setUseCorrelations(true);
    setLookbackBars(40); setInstantEntryConfidence(0.85);
    setMaxPendingBars(10); setPullbackAtrFraction(0.50); setDedupeWindowBars(20);
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
          allowed_regimes: regimeFilter ? allowedRegimes : [
            "trending_up", "trending_down", "ranging", "volatile",
          ],
          use_correlations: useCorrelations,
          ...(agentType === "scout" ? {
            lookback_bars: lookbackBars,
            instant_entry_confidence: instantEntryConfidence,
            max_pending_bars: maxPendingBars,
            pullback_atr_fraction: pullbackAtrFraction,
            dedupe_window_bars: dedupeWindowBars,
          } : {}),
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
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Max Daily Loss (%)</label>
              <input type="number" min="0.5" max="10" step="0.1" value={maxDailyLoss}
                onChange={(e) => { const v = parseFloat(e.target.value); if (!isNaN(v) && v >= 0.5 && v <= 10) setMaxDailyLoss(v); }}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                style={{ borderColor: "var(--border)" }} />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Cooldown (bars)</label>
              <input type="number" min="1" max="20" step="1" value={cooldownBars}
                onChange={(e) => { const v = parseInt(e.target.value); if (!isNaN(v) && v >= 1 && v <= 20) setCooldownBars(v); }}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                style={{ borderColor: "var(--border)" }} />
            </div>
          </div>
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
          <div className="pt-2 border-t" style={{ borderColor: "var(--border)" }}>
            <label className="flex items-center justify-between cursor-pointer">
              <div>
                <p className="text-sm font-medium">Prop Firm Mode</p>
                <p className="text-[10px]" style={{ color: "var(--muted)" }}>
                  FTMO-style tiered DD: yellow −1.5% (size↓), red −2.5% (pause), hard −3%.
                </p>
              </div>
              <input type="checkbox" checked={propFirmEnabled} onChange={(e) => setPropFirmEnabled(e.target.checked)} className="rounded" />
            </label>
          </div>
        </div>
      )}

      {/* Step 3: Filters */}
      {step === 2 && (
        <div className="space-y-4">
          <div>
            <p className="text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Allowed Trade Directions</p>
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

          <div className="pt-3 border-t" style={{ borderColor: "var(--border)" }}>
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-medium" style={{ color: "var(--muted)" }}>Trading Sessions</p>
              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <input type="checkbox" checked={sessionFilter} onChange={(e) => setSessionFilter(e.target.checked)} className="rounded" />
                Restrict to selected
              </label>
            </div>
            <div className={`grid grid-cols-1 gap-1.5 ${!sessionFilter ? "opacity-40 pointer-events-none" : ""}`}>
              {ALL_SESSIONS.map((s) => {
                const checked = allowedSessions.includes(s.id);
                return (
                  <label key={s.id} className={`flex items-center justify-between px-3 py-1.5 rounded-lg border cursor-pointer text-xs hover:bg-white/5 ${checked ? "border-blue-500 bg-blue-500/5" : ""}`}
                    style={{ borderColor: checked ? undefined : "var(--border)" }}>
                    <span className="flex items-center gap-2">
                      <input type="checkbox" checked={checked}
                        onChange={() => setAllowedSessions((prev) => prev.includes(s.id) ? prev.filter((x) => x !== s.id) : [...prev, s.id])}
                        className="rounded" />
                      <span className="font-medium">{s.label}</span>
                    </span>
                    <span style={{ color: "var(--muted)" }}>{s.hours}</span>
                  </label>
                );
              })}
            </div>
          </div>

          <div className="pt-3 border-t" style={{ borderColor: "var(--border)" }}>
            <label className="flex items-center gap-2 text-sm cursor-pointer mb-2">
              <input type="checkbox" checked={regimeFilter} onChange={(e) => setRegimeFilter(e.target.checked)} className="rounded" />
              <span>Regime filter <span className="text-[10px]" style={{ color: "var(--muted)" }}>(skip trades outside allowed market states)</span></span>
            </label>
            {regimeFilter && (
              <div className="ml-6 grid grid-cols-2 gap-x-3 gap-y-1 p-2 rounded border" style={{ borderColor: "var(--border)" }}>
                {ALL_REGIMES.map((r) => (
                  <label key={r.id} className="flex items-start gap-2 text-xs cursor-pointer">
                    <input type="checkbox" checked={allowedRegimes.includes(r.id)}
                      onChange={(e) => {
                        if (e.target.checked) setAllowedRegimes([...allowedRegimes, r.id]);
                        else setAllowedRegimes(allowedRegimes.filter((x) => x !== r.id));
                      }}
                      className="rounded mt-0.5" />
                    <span>
                      <span className="font-medium">{r.label}</span>
                      <span className="block text-[10px]" style={{ color: "var(--muted)" }}>{r.desc}</span>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>

          <div className="pt-3 border-t space-y-2" style={{ borderColor: "var(--border)" }}>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={newsFilter} onChange={(e) => setNewsFilter(e.target.checked)} className="rounded" />
              <span>News filter <span className="text-[10px]" style={{ color: "var(--muted)" }}>(skip 30min before high-impact events)</span></span>
            </label>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={useCorrelations} onChange={(e) => setUseCorrelations(e.target.checked)} className="rounded" />
              <span>Symbol correlations <span className="text-[10px]" style={{ color: "var(--muted)" }}>(include cross-symbol features)</span></span>
            </label>
          </div>

          {agentType === "scout" && (
            <div className="pt-3 border-t" style={{ borderColor: "var(--border)" }}>
              <p className="text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>
                Scout entry state machine
              </p>
              <p className="text-[10px] mb-3" style={{ color: "var(--muted)" }}>
                Stash signal → wait for pullback / BOS / instant-conf / expiry before entering.
              </p>
              <div className="grid grid-cols-2 gap-3">
                <label className="space-y-1 text-xs">
                  <span style={{ color: "var(--muted)" }}>Lookback bars</span>
                  <input type="number" min={10} max={120} step={1} value={lookbackBars}
                    onChange={(e) => setLookbackBars(Math.max(10, Math.min(120, parseInt(e.target.value) || 40)))}
                    className="w-full px-2 py-1.5 rounded-lg border bg-transparent" style={{ borderColor: "var(--border)" }} />
                </label>
                <label className="space-y-1 text-xs">
                  <span style={{ color: "var(--muted)" }}>Instant-entry conf</span>
                  <input type="number" min={0.5} max={0.99} step={0.01} value={instantEntryConfidence}
                    onChange={(e) => setInstantEntryConfidence(Math.max(0.5, Math.min(0.99, parseFloat(e.target.value) || 0.85)))}
                    className="w-full px-2 py-1.5 rounded-lg border bg-transparent" style={{ borderColor: "var(--border)" }} />
                </label>
                <label className="space-y-1 text-xs">
                  <span style={{ color: "var(--muted)" }}>Max pending bars</span>
                  <input type="number" min={2} max={60} step={1} value={maxPendingBars}
                    onChange={(e) => setMaxPendingBars(Math.max(2, Math.min(60, parseInt(e.target.value) || 10)))}
                    className="w-full px-2 py-1.5 rounded-lg border bg-transparent" style={{ borderColor: "var(--border)" }} />
                </label>
                <label className="space-y-1 text-xs">
                  <span style={{ color: "var(--muted)" }}>Pullback (× ATR)</span>
                  <input type="number" min={0.1} max={2} step={0.05} value={pullbackAtrFraction}
                    onChange={(e) => setPullbackAtrFraction(Math.max(0.1, Math.min(2, parseFloat(e.target.value) || 0.5)))}
                    className="w-full px-2 py-1.5 rounded-lg border bg-transparent" style={{ borderColor: "var(--border)" }} />
                </label>
                <label className="space-y-1 text-xs col-span-2">
                  <span style={{ color: "var(--muted)" }}>Dedupe window (bars, 0 = off)</span>
                  <input type="number" min={0} max={100} step={1} value={dedupeWindowBars}
                    onChange={(e) => setDedupeWindowBars(Math.max(0, Math.min(100, parseInt(e.target.value) || 0)))}
                    className="w-full px-2 py-1.5 rounded-lg border bg-transparent" style={{ borderColor: "var(--border)" }} />
                </label>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Step 4: Review */}
      {step === 3 && (
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
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Correlations</span><span>{useCorrelations ? "On" : "Off"}</span>
          </div>
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Directions</span>
            <span>{allowBuy && allowSell ? "Long + Short" : allowBuy ? "Long only" : allowSell ? "Short only" : "None"}</span>
          </div>
          {sessionFilter && (
            <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
              <span style={{ color: "var(--muted)" }}>Sessions</span>
              <span className="text-xs text-right">{allowedSessions.join(", ") || "—"}</span>
            </div>
          )}
          {regimeFilter && (
            <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
              <span style={{ color: "var(--muted)" }}>Regimes</span>
              <span className="text-xs text-right">{allowedRegimes.length === 4 ? "all" : allowedRegimes.join(", ") || "—"}</span>
            </div>
          )}
          <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>Prop Firm</span><span>{propFirmEnabled ? "On" : "Off"}</span>
          </div>
          {agentType === "scout" && (
            <div className="flex justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
              <span style={{ color: "var(--muted)" }}>Scout</span>
              <span className="text-xs text-right">
                lookback={lookbackBars} · instant≥{instantEntryConfidence.toFixed(2)} · pull={pullbackAtrFraction.toFixed(2)}×ATR · max={maxPendingBars}b · dedupe={dedupeWindowBars}b
              </span>
            </div>
          )}
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
