"use client";

import { useState } from "react";
import Card, { StatCard } from "@/components/ui/Card";
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
  error?: string;
}

export default function BacktestPage() {
  const [symbol, setSymbol] = useState<string>("US30");
  const [dataSource, setDataSource] = useState<"history" | "broker">("broker");
  const [datePreset, setDatePreset] = useState<DatePreset>("6m");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [balance, setBalance] = useState(10000);
  const [maxLot, setMaxLot] = useState(0.1);
  const [riskPct, setRiskPct] = useState(1.0);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState("");
  const [result, setResult] = useState<BacktestResult | null>(null);

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
    if (datePreset === "all") return {};
    const now = new Date();
    const months = datePreset === "3m" ? 3 : datePreset === "6m" ? 6 : 12;
    const start = new Date(now);
    start.setMonth(start.getMonth() - months);
    return { start_date: start.toISOString().slice(0, 10) };
  };

  const runBacktest = async () => {
    setLoading(true);
    setResult(null);
    setProgress("Starting...");
    try {
      const dates = getDateRange();
      await api.post("/api/backtest/potential", {
        symbol,
        balance,
        max_lot: maxLot,
        risk_pct: riskPct / 100,
        data_source: dataSource,
        ...dates,
      });
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
            if (graceCycles >= 3) {
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

      {/* Agent Info */}
      <Card>
        <div className="flex items-center gap-3 mb-4">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-blue-500 flex items-center justify-center">
            <Zap size={16} className="text-white" />
          </div>
          <div>
            <h2 className="text-sm font-semibold">Potential Agent v2</h2>
            <p className="text-xs" style={{ color: "var(--muted)" }}>
              85 institutional features, ATR-normalized, Grade A models
            </p>
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
          <div className="grid grid-cols-2 gap-2">
            <button onClick={() => setDataSource("broker")}
              className={`p-2.5 text-center rounded-lg border transition-colors ${dataSource === "broker" ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: dataSource === "broker" ? undefined : "var(--border)" }}>
              <p className="font-medium text-sm">Broker (Live)</p>
              <p className="text-xs" style={{ color: "var(--muted)" }}>Latest 5000 bars from Oanda</p>
            </button>
            <button onClick={() => setDataSource("history")}
              className={`p-2.5 text-center rounded-lg border transition-colors ${dataSource === "history" ? "border-blue-500 bg-blue-500/10" : "hover:bg-white/5"}`}
              style={{ borderColor: dataSource === "history" ? undefined : "var(--border)" }}>
              <p className="font-medium text-sm">Historical</p>
              <p className="text-xs" style={{ color: "var(--muted)" }}>Full history from CSV/Databento</p>
            </button>
          </div>
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

        {/* Parameters */}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
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
          <div>
            <label className="block text-xs font-medium mb-1 flex items-center gap-1" style={{ color: "var(--muted)" }}>
              <BarChart3 size={12} /> Max Lot Size
            </label>
            <input
              type="number"
              step="0.01"
              min="0.01"
              max="10"
              value={maxLot}
              onChange={(e) => setMaxLot(parseFloat(e.target.value) || 0.1)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
              style={{ borderColor: "var(--border)" }}
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1 flex items-center gap-1" style={{ color: "var(--muted)" }}>
              <Shield size={12} /> Risk % per Trade
            </label>
            <input
              type="number"
              step="0.1"
              min="0.1"
              max="5"
              value={riskPct}
              onChange={(e) => setRiskPct(parseFloat(e.target.value) || 1)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
              style={{ borderColor: "var(--border)" }}
            />
          </div>
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
      </Card>

      {/* Results */}
      {result && !result.error && (
        <>
          <p className="text-xs flex items-center gap-1" style={{ color: "var(--muted)" }}>
            Results are temporary -- run again after page refresh.
          </p>
          {/* Model badge */}
          <Card>
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
          </Card>

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
              <Card className={result.total_pnl > 0 ? "!rounded-[10px]" : ""}>
                <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
                  <TrendingUp size={14} style={{ color: "var(--muted)" }} /> Equity Curve
                </h3>
                <EquityCurveChart data={eqCurve} height={220} />
              </Card>
            </div>
          )}

          {/* Monthly Breakdown Table */}
          {result.monthly_breakdown && result.monthly_breakdown.length > 0 && (
            <Card>
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
            </Card>
          )}

          {/* Trade Table */}
          {result.trades && result.trades.length > 0 && (
            <Card>
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
            </Card>
          )}
        </>
      )}

      {result?.error && (
        <Card>
          <p className="text-red-400 text-sm">{result.error}</p>
        </Card>
      )}
    </div>
  );
}
