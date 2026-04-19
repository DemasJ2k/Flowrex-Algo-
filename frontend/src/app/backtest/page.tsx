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

const SYMBOLS = ["US30", "BTCUSD", "XAUUSD", "ES", "NAS100"] as const;
const SYMBOL_META: Record<string, { label: string; desc: string }> = {
  US30: { label: "US30", desc: "Dow Jones" },
  BTCUSD: { label: "BTCUSD", desc: "Bitcoin" },
  XAUUSD: { label: "XAUUSD", desc: "Gold" },
  ES: { label: "ES", desc: "S&P 500" },
  NAS100: { label: "NAS100", desc: "Nasdaq 100" },
};

type DatePreset = "3m" | "6m" | "1y" | "all" | "custom";

interface MonthlyRow {
  month: string;
  pnl: number;
  trades: number;
  win_rate: number;
  cumulative_pnl: number;
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
  const [agentType, setAgentType] = useState<"potential" | "flowrex_v2">("potential");
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
          <div className="grid grid-cols-2 gap-2">
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
          </div>
        </div>

        {/* Symbol Selector */}
        <div className="mb-4">
          <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>
            Symbol
          </label>
          <div className="grid grid-cols-5 gap-2">
            {SYMBOLS.map((s) => (
              <button
                key={s}
                onClick={() => setSymbol(s)}
                className={`px-3 py-2.5 rounded-lg border text-center transition-all ${
                  symbol === s
                    ? "border-violet-500 bg-violet-500/10 text-white"
                    : "border-transparent hover:border-violet-500/30"
                }`}
                style={{
                  borderColor: symbol === s ? undefined : "var(--border)",
                  background: symbol === s ? undefined : "var(--bg)",
                }}
              >
                <div className="text-sm font-medium">{SYMBOL_META[s].label}</div>
                <div className="text-[10px]" style={{ color: "var(--muted)" }}>
                  {SYMBOL_META[s].desc}
                </div>
              </button>
            ))}
          </div>
        </div>

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
