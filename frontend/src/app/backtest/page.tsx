"use client";

import { useEffect, useState } from "react";
import Card, { StatCard } from "@/components/ui/Card";
import Glass from "@/components/ui/Glass";
import DataTable, { Column } from "@/components/ui/DataTable";
import StatusBadge from "@/components/ui/StatusBadge";
import EquityCurveChart from "@/components/EquityCurveChart";
import api from "@/lib/api";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import { FlaskConical, Loader2, TrendingUp, BarChart3, Calendar, DollarSign, Shield, Zap } from "lucide-react";

// Popular symbols shown as the default "favorites" set before any API
// response. Expanded beyond the original 5 with Dukascopy-available
// instruments users have asked for. Additional symbols surface
// dynamically from /api/ml/symbols (deployed models) and the connected
// broker's /api/broker/symbols response.
const POPULAR_SYMBOLS = [
  "US30", "BTCUSD", "XAUUSD", "ES", "NAS100",
  "ETHUSD", "XAGUSD", "AUS200", "GER40", "EURUSD", "GBPUSD", "USDJPY",
] as const;
const SYMBOL_META: Record<string, { label: string; desc: string }> = {
  US30: { label: "US30", desc: "Dow Jones" },
  BTCUSD: { label: "BTCUSD", desc: "Bitcoin" },
  XAUUSD: { label: "XAUUSD", desc: "Gold" },
  ES: { label: "ES", desc: "S&P 500" },
  NAS100: { label: "NAS100", desc: "Nasdaq 100" },
  ETHUSD: { label: "ETHUSD", desc: "Ethereum" },
  XAGUSD: { label: "XAGUSD", desc: "Silver" },
  AUS200: { label: "AUS200", desc: "ASX 200" },
  GER40: { label: "GER40", desc: "DAX" },
  EURUSD: { label: "EURUSD", desc: "Euro / USD" },
  GBPUSD: { label: "GBPUSD", desc: "Cable" },
  USDJPY: { label: "USDJPY", desc: "Dollar / Yen" },
};

type DatePreset = "3m" | "6m" | "1y" | "all" | "custom";

interface MonthlyRow {
  month: string;
  pnl: number;
  trades: number;
  win_rate: number;
  cumulative_pnl: number;
  phase?: "in_sample" | "oos" | "boundary";
  oos_trades?: number;
  in_sample_trades?: number;
}

interface TradeRow {
  direction: string;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  lot_size: number;
  pnl: number;
  exit_reason: string;
  bars_held: number;
}

interface DataWindow {
  source: string;
  broker: string | null;
  first_bar_ts: number | null;
  last_bar_ts: number | null;
  m5_bars_in_window: number;
  requested_start: string | null;
  requested_end: string | null;
  broker_cap: number | null;
}

interface BreakdownEntry {
  trades: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl: number;
}

interface Breakdowns {
  direction?: Record<string, BreakdownEntry>;
  exit_type?: Record<string, BreakdownEntry>;
  session?: Record<string, BreakdownEntry>;
  confidence?: Record<string, BreakdownEntry>;
  oos_split?: Record<string, BreakdownEntry>;
}

interface BacktestResult {
  symbol: string;
  model: string;
  grade: string;
  total_pnl: number;
  total_pnl_pct: number;
  final_balance: number;
  starting_balance: number;
  win_rate: number;
  sharpe_ratio: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  profit_factor: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  avg_win: number;
  avg_loss: number;
  monthly_breakdown: MonthlyRow[];
  equity_curve: { time: number; value: number }[];
  trades: TradeRow[];
  data_window?: DataWindow;
  oos_start_ts?: number;
  breakdowns?: Breakdowns;
  filter_rejections?: {
    session: number;
    regime: number;
    direction: number;
    session_filter_on: boolean;
    regime_filter_on: boolean;
    use_correlations: boolean;
    allowed_sessions: string[];
    allowed_regimes: string[];
    allow_buy: boolean;
    allow_sell: boolean;
  };
  error?: string;
}

// Keep this in sync with BROKER_MAX_CANDLES in backend/app/api/backtest.py.
const BROKER_M5_CAP: Record<string, number> = {
  oanda: 5000,
  ctrader: 5000,
  mt5: 50000,
  tradovate: 5000,
  interactive_brokers: 1000,
};

const BROKER_LABEL: Record<string, string> = {
  oanda: "OANDA",
  ctrader: "cTrader",
  mt5: "MT5",
  tradovate: "Tradovate",
  interactive_brokers: "Interactive Brokers",
};

export default function BacktestPage() {
  const [symbol, setSymbol] = useState<string>("US30");
  const [agentType, setAgentType] = useState<"potential" | "flowrex_v2" | "scout">("potential");
  // Scout-only knobs. Sensible defaults that match the ScoutAgent runtime.
  const [scoutLookbackBars, setScoutLookbackBars] = useState<number>(40);
  const [scoutInstantConf, setScoutInstantConf] = useState<number>(0.85);
  const [scoutMaxPending, setScoutMaxPending] = useState<number>(10);
  const [scoutPullbackAtr, setScoutPullbackAtr] = useState<number>(0.50);
  const [scoutDedupeBars, setScoutDedupeBars] = useState<number>(20);
  // Regime-classifier validation: bar-level regime labels + forward-return
  // breakdown. Shown as a separate card so users can sanity-check the
  // classifier before flipping `regime_filter` on a live agent.
  type RegimeBucket = { n_bars: number; mean_return_pct: number; median_return_pct: number; std_pct: number; up_rate: number; abs_return_pct: number };
  type RegimeValidation = { symbol: string; days: number; forward_bars: number; total_bars: number; classified_bars: number; buckets: Record<string, RegimeBucket> };
  const [regimeValidation, setRegimeValidation] = useState<RegimeValidation | null>(null);
  const [regimeValidating, setRegimeValidating] = useState(false);
  const [regimeDays, setRegimeDays] = useState<number>(90);
  const [regimeForwardBars, setRegimeForwardBars] = useState<number>(10);
  // Filter sandbox — per-run overrides that DO NOT touch any live agent.
  // Lets users A/B test a filter config before flipping the live toggle.
  const [btSessionFilter, setBtSessionFilter] = useState<boolean>(false);
  const [btAllowedSessions, setBtAllowedSessions] = useState<string[]>([
    "london", "ny_open", "ny_close",
  ]);
  const [btRegimeFilter, setBtRegimeFilter] = useState<boolean>(false);
  const [btAllowedRegimes, setBtAllowedRegimes] = useState<string[]>([
    "trending_up", "trending_down", "ranging", "volatile",
  ]);
  const [btUseCorrelations, setBtUseCorrelations] = useState<boolean>(true);
  // Dynamic symbol list — merged from /api/ml/symbols (deployed models) +
  // /api/broker/symbols (what the connected broker actually supports).
  // Falls back to POPULAR_SYMBOLS until both respond.
  type MlSymbol = { symbol: string; asset_class?: string; models?: Array<{ pipeline: string; grade: string }> };
  const [mlSymbols, setMlSymbols] = useState<MlSymbol[]>([]);
  const [brokerSymbols, setBrokerSymbols] = useState<string[]>([]);
  const [symbolSearch, setSymbolSearch] = useState<string>("");
  const [dataSource, setDataSource] = useState<"history" | "broker" | "dukascopy">("dukascopy");
  const [connectedBrokers, setConnectedBrokers] = useState<string[]>([]);
  const [selectedBroker, setSelectedBroker] = useState<string>("");
  const [datePreset, setDatePreset] = useState<DatePreset>("6m");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [balance, setBalance] = useState(10000);
  const [sizingMode, setSizingMode] = useState<"risk_pct" | "max_lots">("risk_pct");
  const [maxLot, setMaxLot] = useState(5);
  const [riskPct, setRiskPct] = useState(0.10);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState("");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [aiMarkdown, setAiMarkdown] = useState<string | null>(null);
  const [resultId, setResultId] = useState<number | null>(null);
  // Cost overrides — pre-filled from /api/backtest/cost-defaults/{symbol}
  // when the user changes symbol. `null` = use backend symbol default.
  const [spreadPts, setSpreadPts] = useState<number | null>(null);
  const [slippagePts, setSlippagePts] = useState<number | null>(null);
  const [commissionPerLot, setCommissionPerLot] = useState<number | null>(null);

  // Load the user's currently-connected brokers so the broker-live picker
  // shows real options (and hides itself if nothing is connected).
  useEffect(() => {
    api.get("/api/broker/status").then((r) => {
      const list: string[] = Array.isArray(r.data?.brokers)
        ? r.data.brokers
        : r.data?.broker ? [r.data.broker] : [];
      setConnectedBrokers(list);
      if (list.length > 0 && !selectedBroker) setSelectedBroker(list[0]);
    }).catch(() => setConnectedBrokers([]));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Deployed-model symbols + broker-supported symbols. Merged for the picker
  // so users can backtest anything we've trained a model for OR anything
  // their broker can stream candles for.
  useEffect(() => {
    api.get("/api/ml/symbols").then((r) => {
      setMlSymbols(Array.isArray(r.data) ? r.data : []);
    }).catch(() => setMlSymbols([]));
    api.get("/api/broker/symbols").then((r) => {
      const names = (r.data as Array<{ name: string }> | undefined)?.map((s) => s.name) || [];
      setBrokerSymbols(names);
    }).catch(() => setBrokerSymbols([]));
  }, []);

  // When the symbol changes, pre-fill cost inputs with backend's symbol
  // default. User can override in the UI; the request sends the overridden
  // values. Skips the fetch / reset if nothing is configured yet.
  useEffect(() => {
    if (!symbol) return;
    api.get(`/api/backtest/cost-defaults/${symbol}`).then((r) => {
      setSpreadPts(r.data?.spread_pts ?? null);
      setSlippagePts(r.data?.slippage_pts ?? null);
      setCommissionPerLot(r.data?.commission_per_lot ?? 0);
    }).catch(() => {
      setSpreadPts(null); setSlippagePts(null); setCommissionPerLot(null);
    });
  }, [symbol]);

  const fmt = (v: number | undefined) =>
    v !== undefined
      ? v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : "\u2014";
  const pnlColor = (v: number) => (v >= 0 ? "green" : "red") as "green" | "red";

  const getDateRange = (): { start_date?: string; end_date?: string } => {
    if (datePreset === "custom") {
      return {
        start_date: customStart || undefined,
        end_date: customEnd || undefined,
      };
    }
    if (datePreset === "all") return { start_date: "2010-01-01" };
    const now = new Date();
    const months = datePreset === "3m" ? 3 : datePreset === "6m" ? 6 : 12;
    const start = new Date(now);
    start.setMonth(start.getMonth() - months);
    return { start_date: start.toISOString().slice(0, 10) };
  };

  const analyzeWithAI = async () => {
    if (!result) return;
    setAnalyzing(true);
    setAiMarkdown(null);
    try {
      const body: Record<string, unknown> = {};
      if (resultId) body.result_id = resultId;
      if (result.symbol) body.symbol = result.symbol;
      const res = await api.post("/api/backtest/analyze", body, { timeout: 60000 });
      setAiMarkdown(res.data?.markdown || "_No response._");
    } catch (e: unknown) {
      toast.error(getErrorMessage(e));
    } finally {
      setAnalyzing(false);
    }
  };

  const runRegimeValidation = async () => {
    setRegimeValidating(true);
    setRegimeValidation(null);
    try {
      const res = await api.post("/api/backtest/regime-validate", {
        symbol,
        days: regimeDays,
        forward_bars: regimeForwardBars,
      }, { timeout: 180000 });
      setRegimeValidation(res.data);
    } catch (e: unknown) {
      toast.error(getErrorMessage(e));
    } finally {
      setRegimeValidating(false);
    }
  };

  const runBacktest = async () => {
    if (balance < 100) { toast.error("Balance must be at least $100"); return; }
    if (sizingMode === "risk_pct" && (riskPct <= 0 || riskPct > 3)) { toast.error("Risk % must be between 0.01 and 3"); return; }
    if (sizingMode === "max_lots" && maxLot <= 0) { toast.error("Max lot must be greater than 0"); return; }
    setLoading(true);
    setResult(null);
    setProgress("Starting...");
    try {
      const dates = getDateRange();
      setAiMarkdown(null);
      setResultId(null);
      const res = await api.post("/api/backtest/potential", {
        symbol,
        agent_type: agentType,
        balance,
        max_lot: sizingMode === "max_lots" ? maxLot : 100,
        risk_pct: sizingMode === "risk_pct" ? riskPct / 100 : 0.01,
        sizing_mode: sizingMode,
        data_source: dataSource,
        broker: dataSource === "broker" ? (selectedBroker || undefined) : undefined,
        // Cost overrides: send only when the user has changed them from the
        // symbol default (non-null). Backend falls back to the symbol's
        // _EXEC_COSTS entry when null / missing.
        spread_pts_override: spreadPts,
        slippage_pts_override: slippagePts,
        commission_per_lot_override: commissionPerLot,
        ...(agentType === "scout" ? {
          lookback_bars: scoutLookbackBars,
          instant_entry_confidence: scoutInstantConf,
          max_pending_bars: scoutMaxPending,
          pullback_atr_fraction: scoutPullbackAtr,
          dedupe_window_bars: scoutDedupeBars,
        } : {}),
        // Filter sandbox — sent regardless of agent type. Live agents
        // are never touched by these values; they're per-run only.
        session_filter: btSessionFilter,
        allowed_sessions: btSessionFilter ? btAllowedSessions : null,
        regime_filter: btRegimeFilter,
        allowed_regimes: btRegimeFilter ? btAllowedRegimes : null,
        use_correlations: btUseCorrelations,
        ...dates,
      });
      if (res.data?.result_id) setResultId(res.data.result_id);
      toast.success("Backtest started for " + symbol);

      let graceCycles = 0;
      const poll = setInterval(async () => {
        try {
          const res = await api.get("/api/backtest/potential/status");
          const status = res.data.running;
          if (status.progress) setProgress(status.progress);

          // Check for results first (primary condition)
          if (res.data.results[symbol]) {
            clearInterval(poll);
            const r = res.data.results[symbol];
            if (r.error) {
              toast.error(r.error);
              setResult(null);
            } else {
              setResult(r as BacktestResult);
            }
            setLoading(false);
            setProgress("");
          } else if (!status.active) {
            // Active is false but no results yet — allow grace cycles
            graceCycles++;
            if (graceCycles >= 10) {
              clearInterval(poll);
              toast.error("Backtest finished but no results were returned");
              setLoading(false);
              setProgress("");
            }
          }
        } catch {
          /* keep polling */
        }
      }, 2000);
      setTimeout(() => {
        clearInterval(poll);
        setLoading(false);
        setProgress("");
      }, 600000);
    } catch (e: unknown) {
      toast.error(getErrorMessage(e));
      setLoading(false);
      setProgress("");
    }
  };

  const eqCurve = (result?.equity_curve || []).map((p) => ({
    time: p.time,
    value: p.value,
  }));

  const monthlyCols: Column<MonthlyRow>[] = [
    { header: "Month", key: "month" },
    {
      header: "Phase",
      key: "phase",
      align: "left",
      render: (r) => {
        const p = r.phase;
        if (!p) return "";
        const cls =
          p === "oos"
            ? "text-emerald-400 border-emerald-500/40"
            : p === "in_sample"
              ? "text-amber-400 border-amber-500/40"
              : "text-blue-400 border-blue-500/40";
        const label = p === "oos" ? "OOS" : p === "in_sample" ? "IS" : "BND";
        return <span className={`text-[10px] px-1.5 py-0.5 rounded-full border ${cls}`}>{label}</span>;
      },
    },
    {
      header: "P&L",
      key: "pnl",
      align: "right",
      render: (r) => (
        <span className={r.pnl >= 0 ? "text-emerald-400 font-medium" : "text-red-400 font-medium"}>
          ${fmt(r.pnl)}
        </span>
      ),
    },
    { header: "Trades", key: "trades", align: "right" },
    {
      header: "Win Rate",
      key: "win_rate",
      align: "right",
      render: (r) => `${r.win_rate.toFixed(1)}%`,
    },
    {
      header: "Cumulative",
      key: "cumulative_pnl",
      align: "right",
      render: (r) => (
        <span className={r.cumulative_pnl >= 0 ? "text-emerald-400 font-medium" : "text-red-400 font-medium"}>
          ${fmt(r.cumulative_pnl)}
        </span>
      ),
    },
  ];

  const tradeCols: Column<TradeRow>[] = [
    {
      header: "Side",
      key: "direction",
      render: (r) => <StatusBadge value={r.direction} />,
    },
    { header: "Entry Time", key: "entry_time" },
    {
      header: "Entry",
      key: "entry_price",
      align: "right",
      render: (r) => fmt(r.entry_price),
    },
    {
      header: "Exit",
      key: "exit_price",
      align: "right",
      render: (r) => fmt(r.exit_price),
    },
    { header: "Lots", key: "lot_size", align: "right" },
    {
      header: "P&L",
      key: "pnl",
      align: "right",
      render: (r) => (
        <span className={r.pnl >= 0 ? "text-emerald-400" : "text-red-400"}>
          ${fmt(r.pnl)}
        </span>
      ),
    },
    {
      header: "Exit",
      key: "exit_reason",
      render: (r) => <StatusBadge value={r.exit_reason} />,
    },
    { header: "Bars", key: "bars_held", align: "right" },
  ];

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold bg-gradient-to-r from-violet-400 to-blue-400 bg-clip-text text-transparent flex items-center gap-2">
        <FlaskConical size={24} /> Backtest
      </h1>

      {/* Agent Selection + Config */}
      <Glass padding="md">
        {/* Agent Type Selector */}
        <div className="mb-4">
          <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Agent / Model</label>
          <div className="grid grid-cols-3 gap-2">
            <button onClick={() => setAgentType("potential")}
              className={`p-3 text-left rounded-lg border transition-colors ${agentType === "potential" ? "border-violet-500 bg-violet-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: agentType === "potential" ? undefined : "var(--border)" }}>
              <div className="flex items-center gap-2 mb-1">
                <div className="w-6 h-6 rounded bg-gradient-to-br from-violet-500 to-blue-500 flex items-center justify-center">
                  <Zap size={12} className="text-white" />
                </div>
                <span className="text-sm font-semibold">Potential Agent v2</span>
              </div>
              <p className="text-[10px]" style={{ color: "var(--muted)" }}>85 institutional features, ATR-normalized, XGBoost + LightGBM</p>
            </button>
            <button onClick={() => setAgentType("flowrex_v2")}
              className={`p-3 text-left rounded-lg border transition-colors ${agentType === "flowrex_v2" ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: agentType === "flowrex_v2" ? undefined : "var(--border)" }}>
              <div className="flex items-center gap-2 mb-1">
                <div className="w-6 h-6 rounded bg-gradient-to-br from-blue-500 to-cyan-500 flex items-center justify-center">
                  <Zap size={12} className="text-white" />
                </div>
                <span className="text-sm font-semibold">Flowrex Agent v2</span>
              </div>
              <p className="text-[10px]" style={{ color: "var(--muted)" }}>120 curated features, 3-model ensemble (XGB + LGB + CatBoost), 4-layer MTF</p>
            </button>
            <button onClick={() => setAgentType("scout")}
              className={`p-3 text-left rounded-lg border transition-colors ${agentType === "scout" ? "border-amber-500 bg-amber-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: agentType === "scout" ? undefined : "var(--border)" }}>
              <div className="flex items-center gap-2 mb-1">
                <div className="w-6 h-6 rounded bg-gradient-to-br from-amber-500 to-orange-500 flex items-center justify-center">
                  <Zap size={12} className="text-white" />
                </div>
                <span className="text-sm font-semibold">Scout Agent</span>
              </div>
              <p className="text-[10px]" style={{ color: "var(--muted)" }}>Potential models + 40-bar lookback, pullback / BOS entry state machine</p>
            </button>
          </div>
        </div>

        {agentType === "scout" && (
          <details className="mb-4 p-3 rounded-lg border" style={{ borderColor: "var(--border)" }}>
            <summary className="cursor-pointer text-xs font-medium" style={{ color: "var(--muted)" }}>
              Scout tuning (lookback · pullback · dedupe)
            </summary>
            <div className="grid grid-cols-2 gap-3 mt-3 text-xs">
              <label className="space-y-1">
                <span style={{ color: "var(--muted)" }}>Lookback bars</span>
                <input type="number" min={10} max={120} step={1}
                  value={scoutLookbackBars}
                  onChange={(e) => setScoutLookbackBars(Math.max(10, Math.min(120, parseInt(e.target.value) || 40)))}
                  className="w-full px-2 py-1.5 rounded-lg border bg-transparent outline-none focus:border-amber-500"
                  style={{ borderColor: "var(--border)" }} />
              </label>
              <label className="space-y-1">
                <span style={{ color: "var(--muted)" }}>Instant-entry conf</span>
                <input type="number" min={0.5} max={0.99} step={0.01}
                  value={scoutInstantConf}
                  onChange={(e) => setScoutInstantConf(Math.max(0.5, Math.min(0.99, parseFloat(e.target.value) || 0.85)))}
                  className="w-full px-2 py-1.5 rounded-lg border bg-transparent outline-none focus:border-amber-500"
                  style={{ borderColor: "var(--border)" }} />
              </label>
              <label className="space-y-1">
                <span style={{ color: "var(--muted)" }}>Max pending bars</span>
                <input type="number" min={2} max={60} step={1}
                  value={scoutMaxPending}
                  onChange={(e) => setScoutMaxPending(Math.max(2, Math.min(60, parseInt(e.target.value) || 10)))}
                  className="w-full px-2 py-1.5 rounded-lg border bg-transparent outline-none focus:border-amber-500"
                  style={{ borderColor: "var(--border)" }} />
              </label>
              <label className="space-y-1">
                <span style={{ color: "var(--muted)" }}>Pullback (× ATR)</span>
                <input type="number" min={0.1} max={2} step={0.05}
                  value={scoutPullbackAtr}
                  onChange={(e) => setScoutPullbackAtr(Math.max(0.1, Math.min(2, parseFloat(e.target.value) || 0.5)))}
                  className="w-full px-2 py-1.5 rounded-lg border bg-transparent outline-none focus:border-amber-500"
                  style={{ borderColor: "var(--border)" }} />
              </label>
              <label className="space-y-1 col-span-2">
                <span style={{ color: "var(--muted)" }}>Dedupe window (bars, 0 = off)</span>
                <input type="number" min={0} max={100} step={1}
                  value={scoutDedupeBars}
                  onChange={(e) => setScoutDedupeBars(Math.max(0, Math.min(100, parseInt(e.target.value) || 0)))}
                  className="w-full px-2 py-1.5 rounded-lg border bg-transparent outline-none focus:border-amber-500"
                  style={{ borderColor: "var(--border)" }} />
              </label>
            </div>
          </details>
        )}

        {/* Symbol Selector — merges popular defaults, deployed-model
            symbols, and the connected broker's symbol list. Search filters
            across all sources. */}
        {(() => {
          // Compose the visible list: popular first (order preserved), then
          // ML-model symbols, then broker symbols. Dedup while preserving
          // insertion order. Each entry tracks its source tags for badges.
          const merged = new Map<string, { sources: Set<string>; grade: string | null; assetClass: string | null }>();
          for (const s of POPULAR_SYMBOLS) {
            merged.set(s, { sources: new Set(["popular"]), grade: null, assetClass: null });
          }
          for (const m of mlSymbols) {
            const entry = merged.get(m.symbol) || { sources: new Set<string>(), grade: null, assetClass: null };
            entry.sources.add("model");
            // Pick best grade across deployed pipelines for a quick hint.
            const grades = (m.models || []).map((x) => x.grade).filter(Boolean);
            const GRADE_ORDER = ["A", "B", "C", "D", "F"];
            for (const g of GRADE_ORDER) {
              if (grades.includes(g)) { entry.grade = g; break; }
            }
            if (m.asset_class) entry.assetClass = m.asset_class;
            merged.set(m.symbol, entry);
          }
          for (const s of brokerSymbols) {
            const entry = merged.get(s) || { sources: new Set<string>(), grade: null, assetClass: null };
            entry.sources.add("broker");
            merged.set(s, entry);
          }
          const q = symbolSearch.trim().toUpperCase();
          const rows = Array.from(merged.entries())
            .filter(([name]) => !q || name.toUpperCase().includes(q));
          const gradeColor = (g: string | null): string => {
            if (g === "A") return "#22c55e";
            if (g === "B") return "#3b82f6";
            if (g === "C") return "#f59e0b";
            if (g === "D") return "#f97316";
            if (g === "F") return "#ef4444";
            return "#71717a";
          };
          return (
            <div className="mb-4">
              <div className="flex items-center justify-between mb-2">
                <label className="block text-xs font-medium" style={{ color: "var(--muted)" }}>
                  Symbol
                </label>
                <input
                  type="text"
                  placeholder="Search (e.g. BTC, EUR, XAG)"
                  value={symbolSearch}
                  onChange={(e) => setSymbolSearch(e.target.value)}
                  className="w-48 px-2 py-1 text-xs rounded-lg border bg-transparent outline-none focus:border-violet-500"
                  style={{ borderColor: "var(--border)" }}
                />
              </div>
              <div className="grid grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2 max-h-72 overflow-y-auto pr-1">
                {rows.map(([name, meta]) => {
                  const label = SYMBOL_META[name]?.label || name;
                  const desc = SYMBOL_META[name]?.desc || meta.assetClass || "—";
                  const active = symbol === name;
                  return (
                    <button
                      key={name}
                      onClick={() => setSymbol(name)}
                      className={`px-3 py-2 rounded-lg border text-center transition-all ${active ? "border-violet-500 bg-violet-500/10 text-white" : "hover:border-violet-500/30"}`}
                      style={{
                        borderColor: active ? undefined : "var(--border)",
                        background: active ? undefined : "var(--bg)",
                      }}
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span className="text-sm font-medium">{label}</span>
                        {meta.grade && (
                          <span
                            className="text-[9px] font-bold px-1 py-0.5 rounded"
                            style={{ background: `${gradeColor(meta.grade)}20`, color: gradeColor(meta.grade) }}
                            title={`Best deployed grade: ${meta.grade}`}
                          >
                            {meta.grade}
                          </span>
                        )}
                      </div>
                      <div className="text-[10px] truncate" style={{ color: "var(--muted)" }}>{desc}</div>
                      <div className="mt-0.5 flex gap-1 justify-center flex-wrap">
                        {meta.sources.has("model") && (
                          <span className="text-[8px] px-1 rounded" style={{ background: "rgba(34,197,94,0.15)", color: "#22c55e" }}>model</span>
                        )}
                        {meta.sources.has("broker") && (
                          <span className="text-[8px] px-1 rounded" style={{ background: "rgba(59,130,246,0.15)", color: "#60a5fa" }}>broker</span>
                        )}
                      </div>
                    </button>
                  );
                })}
                {rows.length === 0 && (
                  <div className="col-span-5 text-center text-xs py-4" style={{ color: "var(--muted)" }}>
                    No symbols match &quot;{symbolSearch}&quot;.
                  </div>
                )}
              </div>
              <p className="text-[10px] mt-1" style={{ color: "var(--muted)" }}>
                <span style={{ color: "#22c55e" }}>model</span> = deployed ML model ·
                {" "}<span style={{ color: "#60a5fa" }}>broker</span> = supported by your connected broker
              </p>
            </div>
          );
        })()}

        {/* Data Source */}
        <div className="mb-4">
          <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Data Source</label>
          <div className="grid grid-cols-3 gap-2">
            <button onClick={() => setDataSource("broker")}
              className={`p-2.5 text-center rounded-lg border transition-colors ${dataSource === "broker" ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: dataSource === "broker" ? undefined : "var(--border)" }}>
              <p className="font-medium text-sm">Broker (Live)</p>
              <p className="text-xs" style={{ color: "var(--muted)" }}>
                {connectedBrokers.length === 0
                  ? "No broker connected"
                  : `Up to ${(BROKER_M5_CAP[selectedBroker || connectedBrokers[0]] || 5000).toLocaleString()} M5 bars`}
              </p>
            </button>
            <button onClick={() => setDataSource("dukascopy")}
              className={`p-2.5 text-center rounded-lg border transition-colors ${dataSource === "dukascopy" ? "border-violet-500 bg-violet-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: dataSource === "dukascopy" ? undefined : "var(--border)" }}>
              <p className="font-medium text-sm">Dukascopy (Fresh)</p>
              <p className="text-xs" style={{ color: "var(--muted)" }}>Fetches fresh data per run</p>
            </button>
            <button onClick={() => setDataSource("history")}
              className={`p-2.5 text-center rounded-lg border transition-colors ${dataSource === "history" ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: dataSource === "history" ? undefined : "var(--border)" }}>
              <p className="font-medium text-sm">Historical (CSV)</p>
              <p className="text-xs" style={{ color: "var(--muted)" }}>Local CSV files on server</p>
            </button>
          </div>

          {/* Broker picker + honest coverage note — only when Broker (Live) is chosen. */}
          {dataSource === "broker" && (
            <div className="mt-2 p-2.5 rounded-lg border" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
              {connectedBrokers.length === 0 ? (
                <p className="text-xs" style={{ color: "var(--muted)" }}>
                  No broker connected. Go to Settings → Broker Connections to connect one.
                </p>
              ) : (
                <div className="flex items-center gap-2 flex-wrap">
                  <label className="text-xs" style={{ color: "var(--muted)" }}>Broker:</label>
                  <select
                    value={selectedBroker}
                    onChange={(e) => setSelectedBroker(e.target.value)}
                    className="px-2 py-1 text-xs rounded border bg-transparent"
                    style={{ borderColor: "var(--border)", background: "var(--background)" }}
                  >
                    {connectedBrokers.map((b) => (
                      <option key={b} value={b}>{BROKER_LABEL[b] || b}</option>
                    ))}
                  </select>
                  <span className="text-[11px]" style={{ color: "var(--muted)" }}>
                    cap: {(BROKER_M5_CAP[selectedBroker] || 5000).toLocaleString()} M5 bars
                    {" \u00b7 "}
                    ~{Math.round(((BROKER_M5_CAP[selectedBroker] || 5000) * 5) / 60 / 24)} days
                  </span>
                </div>
              )}
              <p className="text-[11px] mt-1" style={{ color: "var(--muted)" }}>
                Broker-live is bounded by each broker&apos;s per-request cap. If your date
                range is longer, the backtest uses the earliest bar the broker returned —
                check &quot;Data coverage&quot; after the run.
              </p>
            </div>
          )}
        </div>

        {/* Date Range */}
        <div className="mb-4">
          <label className="block text-xs font-medium mb-2 flex items-center gap-1" style={{ color: "var(--muted)" }}>
            <Calendar size={12} /> Date Range
          </label>
          <div className="flex flex-wrap gap-2 mb-2">
            {(
              [
                { key: "3m", label: "Last 3 months" },
                { key: "6m", label: "Last 6 months" },
                { key: "1y", label: "Last year" },
                { key: "all", label: "All data" },
                { key: "custom", label: "Custom" },
              ] as { key: DatePreset; label: string }[]
            ).map((p) => (
              <button
                key={p.key}
                onClick={() => setDatePreset(p.key)}
                className={`px-3 py-1.5 text-xs rounded-lg border transition-all ${
                  datePreset === p.key
                    ? "border-violet-500 bg-violet-500/10 text-white"
                    : "hover:border-violet-500/30"
                }`}
                style={{
                  borderColor: datePreset === p.key ? undefined : "var(--border)",
                }}
              >
                {p.label}
              </button>
            ))}
          </div>
          {datePreset === "custom" && (
            <div className="flex gap-3 mt-2">
              <div className="flex-1">
                <label className="block text-[10px] mb-1" style={{ color: "var(--muted)" }}>
                  Start
                </label>
                <input
                  type="date"
                  value={customStart}
                  onChange={(e) => setCustomStart(e.target.value)}
                  className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                  style={{ borderColor: "var(--border)" }}
                />
              </div>
              <div className="flex-1">
                <label className="block text-[10px] mb-1" style={{ color: "var(--muted)" }}>
                  End
                </label>
                <input
                  type="date"
                  value={customEnd}
                  onChange={(e) => setCustomEnd(e.target.value)}
                  className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                  style={{ borderColor: "var(--border)" }}
                />
              </div>
            </div>
          )}
        </div>

        {/* Position Sizing Mode */}
        <div className="mb-4">
          <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Position Sizing</label>
          <div className="grid grid-cols-2 gap-2 mb-3">
            <button onClick={() => setSizingMode("risk_pct")}
              className={`p-2 text-center rounded-lg border text-xs transition-colors ${sizingMode === "risk_pct" ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: sizingMode === "risk_pct" ? undefined : "var(--border)" }}>
              Risk % of Balance
            </button>
            <button onClick={() => setSizingMode("max_lots")}
              className={`p-2 text-center rounded-lg border text-xs transition-colors ${sizingMode === "max_lots" ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: sizingMode === "max_lots" ? undefined : "var(--border)" }}>
              Max Lot Size
            </button>
          </div>
        </div>

        {/* Parameters */}
        <div className="grid grid-cols-2 gap-3 mb-4">
          <div>
            <label className="block text-xs font-medium mb-1 flex items-center gap-1" style={{ color: "var(--muted)" }}>
              <DollarSign size={12} /> Starting Balance
            </label>
            <input
              type="number"
              step="1000"
              min="1000"
              max="1000000"
              value={balance}
              onChange={(e) => setBalance(parseFloat(e.target.value) || 10000)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
              style={{ borderColor: "var(--border)" }}
            />
          </div>
          {sizingMode === "risk_pct" ? (
            <div>
              <label className="block text-xs font-medium mb-1 flex items-center gap-1" style={{ color: "var(--muted)" }}>
                <Shield size={12} /> Risk % per Trade
              </label>
              <input
                type="number"
                step="0.01"
                min="0.01"
                max="3"
                value={riskPct}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  if (!isNaN(v) && v >= 0.01 && v <= 3) setRiskPct(v);
                }}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                style={{ borderColor: "var(--border)" }}
              />
            </div>
          ) : (
            <div>
              <label className="block text-xs font-medium mb-1 flex items-center gap-1" style={{ color: "var(--muted)" }}>
                <BarChart3 size={12} /> Max Lot Size
              </label>
              <input
                type="number"
                step="1"
                min="1"
                max="100"
                value={maxLot}
                onChange={(e) => {
                  const v = parseInt(e.target.value);
                  if (!isNaN(v) && v >= 1 && v <= 100) setMaxLot(v);
                }}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                style={{ borderColor: "var(--border)" }}
              />
            </div>
          )}
        </div>

        {/* Execution costs — pre-filled from the symbol default; user can
            override to match their broker (e.g. tighter Oanda live spread or
            FundedNext Bolt Tradovate commission). Null = use backend default. */}
        <details className="mb-4">
          <summary className="text-xs font-medium cursor-pointer select-none" style={{ color: "var(--muted)" }}>
            Execution costs (spread / slippage / commission) — defaults pre-filled for {symbol}
          </summary>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-2">
            <div>
              <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Spread (points)</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={spreadPts ?? ""}
                onChange={(e) => {
                  const t = e.target.value;
                  setSpreadPts(t === "" ? null : parseFloat(t));
                }}
                placeholder="symbol default"
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                style={{ borderColor: "var(--border)" }}
              />
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Slippage (points)</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={slippagePts ?? ""}
                onChange={(e) => {
                  const t = e.target.value;
                  setSlippagePts(t === "" ? null : parseFloat(t));
                }}
                placeholder="symbol default"
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                style={{ borderColor: "var(--border)" }}
              />
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Commission ($/lot, round-trip)</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={commissionPerLot ?? ""}
                onChange={(e) => {
                  const t = e.target.value;
                  setCommissionPerLot(t === "" ? null : parseFloat(t));
                }}
                placeholder="0.00"
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                style={{ borderColor: "var(--border)" }}
              />
            </div>
          </div>
          <p className="text-[10px] mt-1" style={{ color: "var(--muted)" }}>
            Leave blank to use the backend&apos;s symbol default. Commission is
            charged round-trip (entry + exit) per lot.
          </p>
        </details>

        {/* Filter sandbox — per-run overrides (session / regime / correlations)
            that do NOT affect any live agent. */}
        <details className="mb-4 p-3 rounded-lg border" style={{ borderColor: "var(--border)" }}>
          <summary className="cursor-pointer text-xs font-medium" style={{ color: "var(--muted)" }}>
            Filter sandbox — session · regime · correlations (per-run only, never touches live agents)
          </summary>
          <div className="mt-3 space-y-3">
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input type="checkbox" checked={btSessionFilter}
                onChange={(e) => {
                  setBtSessionFilter(e.target.checked);
                  if (e.target.checked && btAllowedSessions.length === 0) {
                    setBtAllowedSessions(["london", "ny_open", "ny_close"]);
                  }
                }}
                className="rounded" />
              <span>Session filter <span style={{ color: "var(--muted)" }}>(skip signals outside selected sessions)</span></span>
            </label>
            {btSessionFilter && (
              <div className="ml-6 grid grid-cols-5 gap-1 text-[10px]">
                {[
                  { id: "asian", label: "Asian" },
                  { id: "london", label: "London" },
                  { id: "ny_open", label: "NY Open" },
                  { id: "ny_close", label: "NY Close" },
                  { id: "off_hours", label: "Off Hours" },
                ].map((s) => {
                  const on = btAllowedSessions.includes(s.id);
                  return (
                    <button key={s.id} type="button"
                      onClick={() => setBtAllowedSessions((prev) => prev.includes(s.id) ? prev.filter((x) => x !== s.id) : [...prev, s.id])}
                      className={`px-2 py-1.5 rounded border ${on ? "border-blue-500 bg-blue-500/10 text-blue-400" : "hover:bg-white/5"}`}
                      style={{ borderColor: on ? undefined : "var(--border)" }}>
                      {s.label}
                    </button>
                  );
                })}
              </div>
            )}

            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input type="checkbox" checked={btRegimeFilter}
                onChange={(e) => {
                  setBtRegimeFilter(e.target.checked);
                  if (e.target.checked && btAllowedRegimes.length === 0) {
                    setBtAllowedRegimes(["trending_up", "trending_down", "ranging", "volatile"]);
                  }
                }}
                className="rounded" />
              <span>Regime filter <span style={{ color: "var(--muted)" }}>(skip signals in non-allowed market states)</span></span>
            </label>
            {btRegimeFilter && (
              <div className="ml-6 grid grid-cols-2 gap-1 text-[10px]">
                {[
                  { id: "trending_up", label: "Trending up" },
                  { id: "trending_down", label: "Trending down" },
                  { id: "ranging", label: "Ranging" },
                  { id: "volatile", label: "Volatile" },
                ].map((r) => {
                  const on = btAllowedRegimes.includes(r.id);
                  return (
                    <button key={r.id} type="button"
                      onClick={() => setBtAllowedRegimes((prev) => prev.includes(r.id) ? prev.filter((x) => x !== r.id) : [...prev, r.id])}
                      className={`px-2 py-1.5 rounded border ${on ? "border-blue-500 bg-blue-500/10 text-blue-400" : "hover:bg-white/5"}`}
                      style={{ borderColor: on ? undefined : "var(--border)" }}>
                      {r.label}
                    </button>
                  );
                })}
              </div>
            )}

            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input type="checkbox" checked={btUseCorrelations} onChange={(e) => setBtUseCorrelations(e.target.checked)} className="rounded" />
              <span>Symbol correlations <span style={{ color: "var(--muted)" }}>(include cross-symbol features in model input)</span></span>
            </label>
          </div>
        </details>

        {/* Regime classifier validation — one-shot sanity check before
            turning regime_filter on live. Renders P&L-relevant next-bar
            stats per regime bucket. */}
        <details className="mb-4">
          <summary className="text-xs font-medium cursor-pointer select-none" style={{ color: "var(--muted)" }}>
            Regime classifier validation — does the classifier correlate with next-bar returns?
          </summary>
          <div className="mt-2 flex items-end gap-3 flex-wrap">
            <div>
              <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Window (days)</label>
              <input type="number" min={7} max={365} step={1}
                value={regimeDays}
                onChange={(e) => setRegimeDays(Math.max(7, Math.min(365, parseInt(e.target.value) || 90)))}
                className="w-28 px-2 py-1.5 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)" }} />
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Forward horizon (M5 bars)</label>
              <input type="number" min={1} max={100} step={1}
                value={regimeForwardBars}
                onChange={(e) => setRegimeForwardBars(Math.max(1, Math.min(100, parseInt(e.target.value) || 10)))}
                className="w-28 px-2 py-1.5 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)" }} />
            </div>
            <button
              onClick={runRegimeValidation}
              disabled={regimeValidating}
              className="px-4 py-2 text-xs font-medium rounded-lg border hover:bg-white/5 disabled:opacity-50"
              style={{ borderColor: "var(--border)" }}
            >
              {regimeValidating ? "Classifying..." : "Run validation"}
            </button>
          </div>
          {regimeValidation && (
            <div className="mt-3 space-y-2">
              <p className="text-[10px]" style={{ color: "var(--muted)" }}>
                {regimeValidation.classified_bars.toLocaleString()} of {regimeValidation.total_bars.toLocaleString()} bars labelled;
                forward window = {regimeValidation.forward_bars} × M5. Look for a regime whose mean return
                is distinct from 0 (edge) or whose std is notably lower (calmer).
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left" style={{ color: "var(--muted)" }}>
                      <th className="py-1 pr-3">Regime</th>
                      <th className="py-1 pr-3 text-right">Bars</th>
                      <th className="py-1 pr-3 text-right">Mean %</th>
                      <th className="py-1 pr-3 text-right">Median %</th>
                      <th className="py-1 pr-3 text-right">Std %</th>
                      <th className="py-1 pr-3 text-right">Up-rate</th>
                      <th className="py-1 pr-3 text-right">|Avg| %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(["trending_up", "trending_down", "ranging", "volatile", "unknown"] as const).map((name) => {
                      const b = regimeValidation.buckets[name];
                      if (!b || b.n_bars === 0) return null;
                      const meanColor = b.mean_return_pct > 0 ? "#34d399" : b.mean_return_pct < 0 ? "#f87171" : "var(--text)";
                      return (
                        <tr key={name} className="border-t" style={{ borderColor: "var(--border)" }}>
                          <td className="py-1 pr-3 font-medium">{name}</td>
                          <td className="py-1 pr-3 text-right tabular-nums">{b.n_bars.toLocaleString()}</td>
                          <td className="py-1 pr-3 text-right tabular-nums" style={{ color: meanColor }}>{b.mean_return_pct.toFixed(3)}</td>
                          <td className="py-1 pr-3 text-right tabular-nums">{b.median_return_pct.toFixed(3)}</td>
                          <td className="py-1 pr-3 text-right tabular-nums">{b.std_pct.toFixed(3)}</td>
                          <td className="py-1 pr-3 text-right tabular-nums">{(b.up_rate * 100).toFixed(1)}%</td>
                          <td className="py-1 pr-3 text-right tabular-nums">{b.abs_return_pct.toFixed(3)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </details>

        {/* Run Button */}
        <button
          onClick={runBacktest}
          disabled={loading}
          className="px-6 py-2.5 text-sm font-medium rounded-lg btn-gradient text-white disabled:opacity-50 flex items-center gap-2"
        >
          {loading ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              {progress ? `Running — ${progress}...` : "Running..."}
            </>
          ) : (
            <>
              <FlaskConical size={14} /> Run Backtest
            </>
          )}
        </button>
      </Glass>

      {/* Results */}
      {result && !result.error && (
        <>
          <p className="text-xs flex items-center gap-1" style={{ color: "var(--muted)" }}>
            Results are temporary -- run again after page refresh.
          </p>
          {/* Model badge */}
          <Glass padding="md">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                  Grade {result.grade}
                </span>
                <span className="text-xs" style={{ color: "var(--muted)" }}>
                  {result.model.toUpperCase()} model
                </span>
              </div>
              <div className="ml-auto text-xs" style={{ color: "var(--muted)" }}>
                {result.total_trades} trades | {result.symbol}
              </div>
            </div>
          </Glass>

          {/* Data coverage — honest bounds of what was actually fetched */}
          {result.data_window && (
            <Glass padding="md">
              <h4 className="text-xs font-semibold mb-1" style={{ color: "var(--muted)" }}>
                Data coverage
              </h4>
              <div className="text-xs grid grid-cols-2 md:grid-cols-4 gap-y-1 gap-x-4">
                <div>
                  <span style={{ color: "var(--muted)" }}>Source: </span>
                  <span className="font-medium">
                    {result.data_window.source}
                    {result.data_window.broker ? ` · ${BROKER_LABEL[result.data_window.broker] || result.data_window.broker}` : ""}
                  </span>
                </div>
                <div>
                  <span style={{ color: "var(--muted)" }}>Window bars: </span>
                  <span className="font-medium">{result.data_window.m5_bars_in_window.toLocaleString()}</span>
                </div>
                <div>
                  <span style={{ color: "var(--muted)" }}>First: </span>
                  <span className="font-medium">
                    {result.data_window.first_bar_ts
                      ? new Date(result.data_window.first_bar_ts * 1000).toISOString().slice(0, 16).replace("T", " ")
                      : "—"}
                  </span>
                </div>
                <div>
                  <span style={{ color: "var(--muted)" }}>Last: </span>
                  <span className="font-medium">
                    {result.data_window.last_bar_ts
                      ? new Date(result.data_window.last_bar_ts * 1000).toISOString().slice(0, 16).replace("T", " ")
                      : "—"}
                  </span>
                </div>
              </div>
              {result.data_window.source === "broker" && result.data_window.broker_cap && (
                <p className="text-[11px] mt-1" style={{ color: "var(--muted)" }}>
                  Broker per-request cap: {result.data_window.broker_cap.toLocaleString()} M5 bars.
                  Requests past that are silently truncated to the earliest bar the broker returned.
                </p>
              )}
            </Glass>
          )}

          {result.filter_rejections && (result.filter_rejections.session_filter_on || result.filter_rejections.regime_filter_on || !result.filter_rejections.use_correlations || !result.filter_rejections.allow_buy || !result.filter_rejections.allow_sell) && (
            <Glass padding="md">
              <p className="text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Filter sandbox impact</p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <div>
                  <span className="block text-[10px]" style={{ color: "var(--muted)" }}>Session rejections</span>
                  <span className="text-lg font-semibold tabular-nums">{result.filter_rejections.session.toLocaleString()}</span>
                  <span className="block text-[10px] mt-0.5" style={{ color: "var(--muted)" }}>
                    {result.filter_rejections.session_filter_on
                      ? `allowed: ${result.filter_rejections.allowed_sessions.join(", ") || "(none)"}`
                      : "filter off"}
                  </span>
                </div>
                <div>
                  <span className="block text-[10px]" style={{ color: "var(--muted)" }}>Regime rejections</span>
                  <span className="text-lg font-semibold tabular-nums">{result.filter_rejections.regime.toLocaleString()}</span>
                  <span className="block text-[10px] mt-0.5" style={{ color: "var(--muted)" }}>
                    {result.filter_rejections.regime_filter_on
                      ? `allowed: ${result.filter_rejections.allowed_regimes.join(", ") || "(none)"}`
                      : "filter off"}
                  </span>
                </div>
                <div>
                  <span className="block text-[10px]" style={{ color: "var(--muted)" }}>Direction rejections</span>
                  <span className="text-lg font-semibold tabular-nums">{(result.filter_rejections.direction ?? 0).toLocaleString()}</span>
                  <span className="block text-[10px] mt-0.5" style={{ color: "var(--muted)" }}>
                    {result.filter_rejections.allow_buy && result.filter_rejections.allow_sell
                      ? "long + short"
                      : result.filter_rejections.allow_buy
                        ? "long only"
                        : result.filter_rejections.allow_sell
                          ? "short only"
                          : "all blocked"}
                  </span>
                </div>
                <div>
                  <span className="block text-[10px]" style={{ color: "var(--muted)" }}>Correlations</span>
                  <span className="text-lg font-semibold">{result.filter_rejections.use_correlations ? "on" : "off"}</span>
                  <span className="block text-[10px] mt-0.5" style={{ color: "var(--muted)" }}>
                    {result.filter_rejections.use_correlations
                      ? "cross-symbol features included"
                      : "cross-symbol features zero-masked"}
                  </span>
                </div>
              </div>
              <p className="text-[10px] mt-2" style={{ color: "var(--muted)" }}>
                These values were applied only to this backtest run. Live agents are unaffected.
              </p>
            </Glass>
          )}

          {/* Summary Stats */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            {[
              <StatCard
                key="pnl"
                label="Total P&L"
                value={`$${fmt(result.total_pnl)}`}
                sub={`${result.total_pnl_pct >= 0 ? "+" : ""}${result.total_pnl_pct}%`}
                color={pnlColor(result.total_pnl)}
              />,
              <StatCard
                key="wr"
                label="Win Rate"
                value={`${result.win_rate}%`}
                sub={`${result.winning_trades}W / ${result.losing_trades}L`}
              />,
              <StatCard key="sr" label="Sharpe Ratio" value={result.sharpe_ratio} />,
              <StatCard
                key="dd"
                label="Max Drawdown"
                value={`$${fmt(result.max_drawdown)}`}
                sub={`${result.max_drawdown_pct}%`}
                color="red"
              />,
              <StatCard key="pf" label="Profit Factor" value={result.profit_factor} />,
              <StatCard key="tt" label="Total Trades" value={result.total_trades} />,
            ].map((card, i) => (
              <div key={i} className="animate-fade-in" style={{ animationDelay: `${i * 0.06}s` }}>
                {card}
              </div>
            ))}
          </div>

          {/* Additional Stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="Final Balance" value={`$${fmt(result.final_balance)}`} color={pnlColor(result.final_balance - result.starting_balance)} />
            <StatCard label="Avg Win" value={`$${fmt(result.avg_win)}`} color="green" />
            <StatCard label="Avg Loss" value={`$${fmt(result.avg_loss)}`} color="red" />
            <StatCard label="Starting Balance" value={`$${fmt(result.starting_balance)}`} />
          </div>

          {/* Equity Curve */}
          {eqCurve.length >= 2 && (
            <div
              className="rounded-xl overflow-hidden"
              style={{
                padding: result.total_pnl > 0 ? "2px" : "0",
                background: result.total_pnl > 0 ? "linear-gradient(135deg, #8b5cf6, #3b82f6)" : "transparent",
              }}
            >
              <Glass padding="md" className={result.total_pnl > 0 ? "!rounded-[10px]" : ""}>
                <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
                  <TrendingUp size={14} style={{ color: "var(--muted)" }} /> Equity Curve
                </h3>
                {result.oos_start_ts && (
                  <p className="text-[11px] mb-1" style={{ color: "var(--muted)" }}>
                    Training OOS cutoff:{" "}
                    <span className="font-medium" style={{ color: "var(--foreground)" }}>
                      {new Date(result.oos_start_ts * 1000).toISOString().slice(0, 10)}
                    </span>
                    {" · "}results before this date are in-sample (the model saw them during training).
                  </p>
                )}
                <EquityCurveChart data={eqCurve} height={220} />
              </Glass>
            </div>
          )}

          {/* In-sample vs OOS split — the bit that matters for overfitting */}
          {result.breakdowns?.oos_split && (
            <Glass padding="md">
              <h3 className="text-sm font-medium mb-2">In-sample vs True OOS</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {(["in_sample", "oos"] as const).map((k) => {
                  const e = result.breakdowns?.oos_split?.[k];
                  if (!e) return null;
                  const label = k === "oos" ? "True OOS (after training cutoff)" : "In-sample (during training)";
                  return (
                    <div key={k} className="rounded-lg p-3 border" style={{ borderColor: "var(--border)" }}>
                      <div className="text-[11px] mb-1" style={{ color: "var(--muted)" }}>{label}</div>
                      <div className="text-lg font-semibold tabular-nums" style={{ color: e.total_pnl >= 0 ? "#10b981" : "#ef4444" }}>
                        ${fmt(e.total_pnl)}
                      </div>
                      <div className="text-xs" style={{ color: "var(--muted)" }}>
                        {e.trades} trades · WR {e.win_rate}% · avg ${fmt(e.avg_pnl)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </Glass>
          )}

          {/* Breakdown cards — direction / exit_type / session / confidence */}
          {result.breakdowns && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {(
                [
                  ["direction", "By direction"],
                  ["exit_type", "By exit type"],
                  ["session", "By session (UTC)"],
                  ["confidence", "By confidence bucket"],
                ] as const
              ).map(([key, label]) => {
                const group = (result.breakdowns as unknown as Record<string, Record<string, BreakdownEntry>>)?.[key] || {};
                const nonEmpty = Object.entries(group).filter(([, v]) => (v?.trades || 0) > 0);
                if (nonEmpty.length === 0) return null;
                return (
                  <Glass key={key} padding="md">
                    <h4 className="text-xs font-semibold mb-2" style={{ color: "var(--muted)" }}>{label}</h4>
                    <div className="space-y-1.5">
                      {nonEmpty.map(([k, v]) => (
                        <div key={k} className="flex items-baseline justify-between text-xs">
                          <span className="font-medium">{k}</span>
                          <span className="tabular-nums" style={{ color: v.total_pnl >= 0 ? "#10b981" : "#ef4444" }}>
                            ${fmt(v.total_pnl)} · {v.trades}t · WR {v.win_rate}%
                          </span>
                        </div>
                      ))}
                    </div>
                  </Glass>
                );
              })}
            </div>
          )}

          {/* Analyse with AI */}
          <Glass padding="md">
            <div className="flex items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-medium">AI analysis</h3>
                <p className="text-[11px]" style={{ color: "var(--muted)" }}>
                  Sends the stats + breakdowns to your configured Claude supervisor for a
                  written review. Requires the AI Supervisor to be enabled in Settings.
                </p>
              </div>
              <button
                onClick={analyzeWithAI}
                disabled={analyzing}
                className="px-3 py-2 text-xs font-medium rounded-lg bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-50 whitespace-nowrap"
              >
                {analyzing ? "Analysing..." : (aiMarkdown ? "Re-run analysis" : "Analyse with AI")}
              </button>
            </div>
            {aiMarkdown && (
              <div
                className="mt-3 max-h-[400px] overflow-y-auto text-sm whitespace-pre-wrap leading-relaxed"
                style={{ color: "var(--foreground)" }}
              >
                {aiMarkdown}
              </div>
            )}
          </Glass>

          {/* Monthly Breakdown Table */}
          {result.monthly_breakdown && result.monthly_breakdown.length > 0 && (
            <Glass padding="md">
              <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
                <BarChart3 size={14} style={{ color: "var(--muted)" }} /> Monthly Breakdown
              </h3>
              <DataTable
                columns={monthlyCols as unknown as Column<Record<string, unknown>>[]}
                data={result.monthly_breakdown as unknown as Record<string, unknown>[]}
                paginated
                pageSize={12}
                emptyMessage="No monthly data"
              />
            </Glass>
          )}

          {/* Trade Table */}
          {result.trades && result.trades.length > 0 && (
            <Glass padding="md">
              <h3 className="text-sm font-medium mb-2">
                Recent Trades ({result.trades.length})
              </h3>
              <DataTable
                columns={tradeCols as unknown as Column<Record<string, unknown>>[]}
                data={result.trades as unknown as Record<string, unknown>[]}
                paginated
                pageSize={25}
                emptyMessage="No trades"
              />
            </Glass>
          )}
        </>
      )}

      {result?.error && (
        <Glass padding="md">
          <p className="text-red-400 text-sm">{result.error}</p>
        </Glass>
      )}
    </div>
  );
}
