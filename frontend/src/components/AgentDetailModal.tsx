"use client";

import { useState, useEffect, useCallback } from "react";
import Modal from "@/components/ui/Modal";
import Tabs from "@/components/ui/Tabs";
import { toSydneyTime } from "@/lib/timezone";
import { StatCard } from "@/components/ui/Card";
import DataTable, { Column } from "@/components/ui/DataTable";
import StatusBadge from "@/components/ui/StatusBadge";
import EquityCurveChart from "@/components/EquityCurveChart";
import api from "@/lib/api";
import type { Agent, AgentPerformance, AgentTrade, AgentLog } from "@/types";
import { Loader2, BarChart3 } from "lucide-react";

interface AnalyticsData {
  overall: Record<string, number>;
  by_session: Record<string, { trades: number; win_rate: number; avg_pnl: number; total_pnl: number }>;
  by_confidence: Record<string, { trades: number; win_rate: number; avg_pnl: number; total_pnl: number }>;
  by_mtf_score: Record<string, { trades: number; win_rate: number; avg_pnl: number; total_pnl: number }>;
  by_direction: Record<string, { trades: number; win_rate: number; avg_pnl: number; total_pnl: number }>;
  by_exit_reason: Record<string, { trades: number; win_rate: number; avg_pnl: number; total_pnl: number }>;
  streaks: { current: { type: string; count: number }; max_winning: number; max_losing: number };
}

function BarRow({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-20 truncate" style={{ color: "var(--muted)" }}>{label}</span>
      <div className="flex-1 h-4 rounded" style={{ background: "var(--card-hover)" }}>
        <div className="h-full rounded" style={{ width: `${pct}%`, background: color, minWidth: pct > 0 ? "4px" : "0" }} />
      </div>
      <span className="w-14 text-right font-mono">{value.toFixed(1)}%</span>
    </div>
  );
}

function AnalyticsSection({ title, data }: {
  title: string;
  data: Record<string, { trades: number; win_rate: number; avg_pnl: number; total_pnl: number }>;
}) {
  const entries = Object.entries(data);
  if (entries.length === 0) return null;
  const maxWr = Math.max(...entries.map(([, v]) => v.win_rate), 1);

  return (
    <div>
      <h4 className="text-xs font-semibold mb-2" style={{ color: "var(--muted)" }}>{title}</h4>
      <div className="space-y-1">
        {entries.map(([k, v]) => (
          <div key={k} className="flex items-center gap-2 text-xs">
            <span className="w-20 truncate font-medium">{k}</span>
            <div className="flex-1 h-4 rounded" style={{ background: "var(--card-hover)" }}>
              <div className="h-full rounded" style={{
                width: `${Math.min(100, (v.win_rate / maxWr) * 100)}%`,
                background: v.win_rate >= 50 ? "#10b981" : "#ef4444",
                minWidth: v.trades > 0 ? "4px" : "0",
              }} />
            </div>
            <span className="w-12 text-right font-mono">{v.win_rate.toFixed(0)}%</span>
            <span className="w-10 text-right" style={{ color: "var(--muted)" }}>{v.trades}t</span>
            <span className={`w-16 text-right font-mono ${v.total_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
              {v.total_pnl >= 0 ? "+" : ""}{v.total_pnl.toFixed(0)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function AgentDetailModal({
  agent,
  open,
  onClose,
  onEdit,
}: {
  agent: Agent | null;
  open: boolean;
  onClose: () => void;
  onEdit?: () => void;
}) {
  const [perf, setPerf] = useState<AgentPerformance | null>(null);
  const [trades, setTrades] = useState<AgentTrade[]>([]);
  const [logs, setLogs] = useState<AgentLog[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [logFilter, setLogFilter] = useState("all");
  const [logSearch, setLogSearch] = useState("");

  const fetchData = useCallback(async () => {
    if (!agent) return;
    setLoading(true);
    try {
      const [perfRes, tradesRes, logsRes, analyticsRes] = await Promise.all([
        api.get(`/api/agents/${agent.id}/performance`),
        api.get(`/api/agents/${agent.id}/trades?limit=200`),
        api.get(`/api/agents/${agent.id}/logs?limit=100`),
        api.get(`/api/agents/${agent.id}/analytics`).catch(() => ({ data: null })),
      ]);
      setPerf(perfRes.data);
      setTrades(tradesRes.data);
      setLogs(logsRes.data);
      setAnalytics(analyticsRes.data);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [agent]);

  useEffect(() => { if (open && agent) fetchData(); }, [open, agent, fetchData]);

  const fmt = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const pnlColor = (v: number) => v >= 0 ? "text-emerald-400" : "text-red-400";

  if (!agent) return null;

  const equityCurve = perf?.equity_curve?.map((p) => ({
    time: Math.floor(new Date(p.time).getTime() / 1000),
    value: p.pnl,
  })) || [];

  const filteredLogs = logs.filter((l) => {
    if (logFilter !== "all" && l.level !== logFilter) return false;
    if (logSearch && !l.message.toLowerCase().includes(logSearch.toLowerCase())) return false;
    return true;
  });

  const tradeCols: Column<AgentTrade>[] = [
    { header: "Symbol", key: "symbol" },
    { header: "Side", key: "direction", render: (r) => <StatusBadge value={r.direction} /> },
    { header: "Size", key: "lot_size", align: "right" },
    { header: "Entry", key: "entry_price", align: "right", render: (r) => fmt(r.entry_price) },
    { header: "SL", key: "stop_loss", align: "right", render: (r) => r.stop_loss !== null && r.stop_loss !== undefined ? <span className="text-red-400">{fmt(r.stop_loss)}</span> : <span style={{ color: "var(--muted)" }}>{"\u2014"}</span> },
    { header: "TP", key: "take_profit", align: "right", render: (r) => r.take_profit !== null && r.take_profit !== undefined ? <span className="text-emerald-400">{fmt(r.take_profit)}</span> : <span style={{ color: "var(--muted)" }}>{"\u2014"}</span> },
    { header: "Exit", key: "exit_price", align: "right", render: (r) => r.exit_price ? fmt(r.exit_price) : "\u2014" },
    { header: "P&L", key: "pnl", align: "right", render: (r) => {
      const p = r.broker_pnl ?? r.pnl ?? 0;
      return <span className={pnlColor(p)}>{p >= 0 ? "+" : ""}{fmt(p)}</span>;
    }},
    { header: "Reason", key: "exit_reason", render: (r) => r.exit_reason ? <StatusBadge value={r.exit_reason} /> : <span style={{ color: "var(--muted)" }}>{"\u2014"}</span> },
    { header: "Status", key: "status", render: (r) => <StatusBadge value={r.status} /> },
  ];

  return (
    <Modal open={open} onClose={onClose} title={agent.name} width="max-w-3xl">
      {/* Agent Header */}
      <div className="flex items-center gap-2 mb-4">
        <StatusBadge value={agent.status} />
        <StatusBadge value={agent.agent_type} />
        <StatusBadge value={agent.mode} />
        <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: "var(--sidebar-active)", color: "var(--muted)" }}>{agent.symbol}</span>
        <span className="text-xs" style={{ color: "var(--muted)" }}>{agent.broker_name} / {agent.timeframe || "M5"}</span>
        {onEdit && (
          <button onClick={onEdit} className="ml-auto text-xs text-blue-400 hover:text-blue-300">Edit Config</button>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-48">
          <Loader2 size={24} className="animate-spin" style={{ color: "var(--muted)" }} />
        </div>
      ) : (
        <Tabs tabs={[
          {
            label: "Performance",
            content: perf ? (
              <div className="space-y-4">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  <StatCard label="Total P&L" value={(perf.total_pnl >= 0 ? "+" : "") + fmt(perf.total_pnl)} color={perf.total_pnl >= 0 ? "green" : "red"} />
                  <StatCard label="Win Rate" value={perf.win_rate.toFixed(1) + "%"} sub={perf.closed_trades + " closed"} />
                  <StatCard label="Sharpe Ratio" value={perf.sharpe_ratio.toFixed(2)} />
                  <StatCard label="Max Drawdown" value={fmt(perf.max_drawdown)} color="red" />
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  <StatCard label="Profit Factor" value={typeof perf.profit_factor === "number" && isFinite(perf.profit_factor) ? perf.profit_factor.toFixed(2) : "\u221E"} />
                  <StatCard label="Avg Win" value={"+" + fmt(perf.avg_win)} color="green" />
                  <StatCard label="Avg Loss" value={fmt(perf.avg_loss)} color="red" />
                  <StatCard label="Avg P&L/Trade" value={fmt(perf.avg_pnl_per_trade)} color={perf.avg_pnl_per_trade >= 0 ? "green" : "red"} />
                </div>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-xs" style={{ color: "var(--muted)" }}>
                  <span>Best Trade: <span className="text-emerald-400 font-medium">{perf.best_trade >= 0 ? "+" : ""}{fmt(perf.best_trade)}</span></span>
                  <span>Worst Trade: <span className="text-red-400 font-medium">{fmt(perf.worst_trade)}</span></span>
                  <span>Win Streak: <span className="text-white font-medium">{perf.max_win_streak}</span></span>
                  <span>Loss Streak: <span className="text-white font-medium">{perf.max_loss_streak}</span></span>
                  <span>Open: <span className="text-white font-medium">{perf.open_trades}</span> / Total: <span className="text-white font-medium">{perf.total_trades}</span></span>
                </div>
                {equityCurve.length >= 2 ? (
                  <EquityCurveChart data={equityCurve} height={160} />
                ) : (
                  <div className="h-[160px] flex items-center justify-center text-sm" style={{ color: "var(--muted)" }}>
                    Not enough data for equity curve
                  </div>
                )}
              </div>
            ) : <p className="text-sm" style={{ color: "var(--muted)" }}>No performance data</p>,
          },
          {
            label: "Analytics",
            content: analytics && analytics.overall?.total_trades ? (
              <div className="space-y-4">
                {/* Overall stats */}
                <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                  <StatCard label="Trades" value={String(analytics.overall.total_trades)} />
                  <StatCard label="Win Rate" value={`${analytics.overall.win_rate}%`} color={analytics.overall.win_rate >= 50 ? "green" : "red"} />
                  <StatCard label="Profit Factor" value={String(analytics.overall.profit_factor || 0)} />
                  <StatCard label="Total P&L" value={`$${analytics.overall.total_pnl}`} color={analytics.overall.total_pnl >= 0 ? "green" : "red"} />
                  <StatCard label="Avg P&L" value={`$${analytics.overall.avg_pnl}`} color={analytics.overall.avg_pnl >= 0 ? "green" : "red"} />
                </div>

                {/* Streaks */}
                {analytics.streaks && (
                  <div className="flex items-center gap-4 text-xs" style={{ color: "var(--muted)" }}>
                    <span>Current: <span className={analytics.streaks.current?.type === "winning" ? "text-emerald-400" : "text-red-400"}>
                      {analytics.streaks.current?.count || 0} {analytics.streaks.current?.type || "none"}
                    </span></span>
                    <span>Max Win Streak: <span className="text-emerald-400 font-medium">{analytics.streaks.max_winning}</span></span>
                    <span>Max Loss Streak: <span className="text-red-400 font-medium">{analytics.streaks.max_losing}</span></span>
                  </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <AnalyticsSection title="By Session" data={analytics.by_session} />
                  <AnalyticsSection title="By Confidence" data={analytics.by_confidence} />
                  <AnalyticsSection title="By Direction" data={analytics.by_direction} />
                  <AnalyticsSection title="By Exit Reason" data={analytics.by_exit_reason} />
                  <AnalyticsSection title="By MTF Score" data={analytics.by_mtf_score} />
                </div>
              </div>
            ) : (
              <div className="text-center py-8" style={{ color: "var(--muted)" }}>
                <BarChart3 size={32} className="mx-auto mb-2 opacity-30" />
                <p className="text-sm">No analytics data yet</p>
                <p className="text-xs mt-1">Analytics populate as trades close</p>
              </div>
            ),
          },
          {
            label: "Trades",
            badge: trades.length,
            content: <DataTable columns={tradeCols as unknown as Column<Record<string, unknown>>[]} data={trades as unknown as Record<string, unknown>[]} emptyMessage="No trades" paginated pageSize={25} />,
          },
          {
            label: "Logs",
            badge: filteredLogs.length,
            content: (
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <select value={logFilter} onChange={(e) => setLogFilter(e.target.value)}
                    className="px-2 py-1 text-xs rounded border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                    <option value="all">All</option>
                    <option value="info">Info</option>
                    <option value="warn">Warn</option>
                    <option value="error">Error</option>
                    <option value="signal">Signal</option>
                    <option value="trade">Trade</option>
                  </select>
                  <input value={logSearch} onChange={(e) => setLogSearch(e.target.value)} placeholder="Search..."
                    className="flex-1 px-2 py-1 text-xs rounded border bg-transparent outline-none" style={{ borderColor: "var(--border)" }} />
                </div>
                <div className="max-h-64 overflow-y-auto text-xs font-mono space-y-0.5"
                     role="log" aria-live="polite" aria-label="Agent log entries">
                  {filteredLogs.length === 0 ? (
                    <p className="py-4 text-center" style={{ color: "var(--muted)" }}>No matching logs</p>
                  ) : (
                    filteredLogs.map((l) => (
                      <div key={l.id} className="flex items-start gap-2 py-1">
                        <span className="flex-shrink-0" style={{ color: "var(--muted)" }}>{toSydneyTime(l.created_at + (l.created_at.includes("Z") || l.created_at.includes("+") ? "" : "Z"))}</span>
                        <StatusBadge value={l.level} />
                        <span className="flex-1 min-w-0 break-all whitespace-pre-wrap max-h-32 overflow-y-auto block">
                          {l.message}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            ),
          },
        ]} />
      )}
    </Modal>
  );
}
