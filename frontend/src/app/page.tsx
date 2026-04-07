"use client";

import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import LandingPage from "@/components/LandingPage";
import { StatCard } from "@/components/ui/Card";
import Card from "@/components/ui/Card";
import StatusBadge from "@/components/ui/StatusBadge";
import SparklineChart from "@/components/ui/SparklineChart";
import EquityCurveChart from "@/components/EquityCurveChart";
import BrokerModal from "@/components/BrokerModal";
import api from "@/lib/api";
import type { AccountInfo, PnlSummaryItem, Agent, AgentTrade, EngineLog, BrokerStatus, LivePosition, MLModel } from "@/types";
import { Bot, LineChart, Plus, Loader2, Plug, FlaskConical, BrainCircuit, TrendingUp } from "lucide-react";

function timeAgo(dateStr: string): string {
  const diff = (Date.now() - new Date(dateStr).getTime()) / 1000;
  if (diff < 60) return Math.floor(diff) + "s ago";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  return Math.floor(diff / 86400) + "d ago";
}

export default function HomePage() {
  const [isAuthed, setIsAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    setIsAuthed(!!localStorage.getItem("access_token"));
  }, []);

  if (isAuthed === null) return null; // loading
  if (!isAuthed) return <LandingPage />;

  return <DashboardView />;
}

function DashboardView() {
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [pnl, setPnl] = useState<PnlSummaryItem[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [trades, setTrades] = useState<AgentTrade[]>([]);
  const [logs, setLogs] = useState<EngineLog[]>([]);
  const [positions, setPositions] = useState<LivePosition[]>([]);
  const [models, setModels] = useState<MLModel[]>([]);
  const [broker, setBroker] = useState<BrokerStatus>({ connected: false, broker: null });
  const [loading, setLoading] = useState(true);
  const [brokerModal, setBrokerModal] = useState(false);

  useEffect(() => {
    Promise.all([
      api.get("/api/broker/status").then((r) => setBroker(r.data)).catch(() => {}),
      api.get("/api/broker/account").then((r) => setAccount(r.data)).catch(() => {}),
      api.get("/api/broker/positions").then((r) => setPositions(r.data)).catch(() => {}),
      api.get("/api/agents").then((r) => setAgents(r.data)).catch(() => {}),
      api.get("/api/agents/pnl-summary").then((r) => setPnl(r.data)).catch(() => {}),
      api.get("/api/agents/all-trades?limit=100").then((r) => setTrades(r.data)).catch(() => {}),
      api.get("/api/agents/engine-logs?limit=10").then((r) => setLogs(r.data)).catch(() => {}),
      api.get("/api/ml/models").then((r) => setModels(r.data)).catch(() => {}),
    ]).finally(() => setLoading(false));
  }, []);

  const fmt = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const pnlColor = (v: number) => (v > 0 ? "green" : v < 0 ? "red" : "default") as "green" | "red" | "default";

  const totalPnl = useMemo(() => pnl.reduce((s, p) => s + p.total_pnl, 0), [pnl]);
  const totalTrades = useMemo(() => pnl.reduce((s, p) => s + p.trade_count, 0), [pnl]);
  const totalWins = useMemo(() => pnl.reduce((s, p) => s + p.win_count, 0), [pnl]);
  const winRate = totalTrades > 0 ? (totalWins / totalTrades * 100) : 0;

  const todayPnl = useMemo(() => {
    const today = new Date().toISOString().split("T")[0];
    return trades
      .filter((t) => t.exit_time?.startsWith(today) && t.status === "closed")
      .reduce((s, t) => s + (t.broker_pnl ?? t.pnl ?? 0), 0);
  }, [trades]);

  const equityCurve = useMemo(() => {
    const closed = trades
      .filter((t) => t.status === "closed" && t.exit_time)
      .sort((a, b) => new Date(a.exit_time!).getTime() - new Date(b.exit_time!).getTime());
    let cum = 0;
    return closed.map((t) => {
      cum += t.broker_pnl ?? t.pnl ?? 0;
      return { time: Math.floor(new Date(t.exit_time!).getTime() / 1000), value: Math.round(cum * 100) / 100 };
    });
  }, [trades]);

  const activeAgents = agents.filter((a) => a.status === "running").length;

  const avgGrade = useMemo(() => {
    const gradeMap: Record<string, number> = { A: 4, B: 3, C: 2, D: 1, F: 0 };
    const gradeReverse: Record<number, string> = { 4: "A", 3: "B", 2: "C", 1: "D", 0: "F" };
    const graded = models.filter((m) => m.grade);
    if (graded.length === 0) return null;
    const avg = graded.reduce((s, m) => s + (gradeMap[m.grade!] ?? 0), 0) / graded.length;
    return gradeReverse[Math.round(avg)] || "C";
  }, [models]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: "var(--muted)" }} />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <div className="flex gap-2">
          <Link href="/trading" className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white">
            <LineChart size={16} /> Trading
          </Link>
          <Link href="/agents" className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>
            <Plus size={16} /> New Agent
          </Link>
        </div>
      </div>

      {/* Section 1: Broker Banner */}
      {!broker.connected ? (
        <div className="flex items-center justify-between px-4 py-3 rounded-lg border border-amber-500/30 bg-amber-500/10">
          <div className="flex items-center gap-2 text-amber-400 text-sm">
            <Plug size={16} />
            <span>No broker connected</span>
          </div>
          <button onClick={() => setBrokerModal(true)} className="px-3 py-1.5 text-xs font-medium rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/30">
            Connect Broker
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-emerald-400 text-sm">
          <span className="w-2 h-2 rounded-full bg-emerald-400" />
          <span>Connected to {broker.broker}</span>
          {account && <span style={{ color: "var(--muted)" }}>| {fmt(account.balance)} {account.currency}</span>}
        </div>
      )}

      {/* Section 2: Portfolio Stats */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatCard label="Balance" value={account ? fmt(account.balance) : "\u2014"} sub={account?.currency || ""} />
        <StatCard label="Equity" value={account ? fmt(account.equity) : "\u2014"} />
        <StatCard label="Today P&L" value={fmt(todayPnl)} color={pnlColor(todayPnl)} />
        <StatCard label="Total P&L" value={totalPnl !== 0 ? (totalPnl >= 0 ? "+" : "") + fmt(totalPnl) : "\u2014"} color={pnlColor(totalPnl)} sub={totalTrades + " trades"} />
        <StatCard label="Win Rate" value={totalTrades > 0 ? winRate.toFixed(1) + "%" : "\u2014"} sub={totalWins + "W / " + (totalTrades - totalWins) + "L"} />
        <StatCard label="Open Positions" value={positions.length} sub={activeAgents + " agents running"} />
      </div>

      {/* Section 3: Equity Curve */}
      <Card>
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-medium flex items-center gap-2">
            <TrendingUp size={16} style={{ color: "var(--muted)" }} /> Equity Curve
          </h2>
          {equityCurve.length > 0 && (
            <span className={"text-sm font-semibold " + (totalPnl >= 0 ? "text-emerald-400" : "text-red-400")}>
              {totalPnl >= 0 ? "+" : ""}{fmt(totalPnl)}
            </span>
          )}
        </div>
        {equityCurve.length >= 2 ? (
          <EquityCurveChart data={equityCurve} height={180} />
        ) : (
          <div className="flex items-center justify-center h-[180px] text-sm" style={{ color: "var(--muted)" }}>
            No closed trades yet
          </div>
        )}
      </Card>

      {/* Section 4: Recent Activity + Quick Actions */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Card className="lg:col-span-3">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium">Recent Activity</h2>
            <Link href="/trading" className="text-xs text-blue-400 hover:text-blue-300">View All</Link>
          </div>
          {logs.length === 0 ? (
            <p className="text-sm py-4 text-center" style={{ color: "var(--muted)" }}>No activity yet</p>
          ) : (
            <div className="space-y-1">
              {logs.map((log) => (
                <div key={log.id} className="flex items-start gap-2 text-xs py-1.5 border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                  <span className="flex-shrink-0 w-14 text-right" style={{ color: "var(--muted)" }}>{timeAgo(log.created_at)}</span>
                  <StatusBadge value={log.level} />
                  <span className="truncate">{log.message}</span>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card className="lg:col-span-2">
          <h2 className="text-sm font-medium mb-3">Quick Actions</h2>
          <div className="space-y-2">
            <Link href="/trading" className="flex items-center gap-2 w-full px-3 py-2.5 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium">
              <LineChart size={16} /> Trading Terminal
            </Link>
            <Link href="/agents" className="flex items-center gap-2 w-full px-3 py-2.5 text-sm rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>
              <Plus size={16} /> Create New Agent
            </Link>
            <Link href="/backtest" className="flex items-center gap-2 w-full px-3 py-2.5 text-sm rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>
              <FlaskConical size={16} /> Run Backtest
            </Link>
            <Link href="/models" className="flex items-center gap-2 w-full px-3 py-2.5 text-sm rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>
              <BrainCircuit size={16} /> ML Models
            </Link>
          </div>
        </Card>
      </div>

      {/* Section 5: Agent Performance Grid */}
      <div>
        <h2 className="text-sm font-medium mb-3" style={{ color: "var(--muted)" }}>Agent Performance</h2>
        {agents.length === 0 ? (
          <Card className="text-center py-8">
            <Bot size={32} className="mx-auto mb-2 opacity-30" />
            <p className="text-sm" style={{ color: "var(--muted)" }}>
              No agents yet.{" "}
              <Link href="/agents" className="text-blue-400 hover:text-blue-300">Create one</Link>
            </p>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {agents.map((agent) => {
              const agentPnl = pnl.find((p) => p.agent_id === agent.id);
              const agentTrades = trades.filter((t) => t.agent_id === agent.id);
              const sparkData = agentTrades.filter((t) => t.status === "closed").slice(-20).map((t) => t.broker_pnl ?? t.pnl ?? 0);
              const wr = agentPnl && agentPnl.trade_count > 0 ? ((agentPnl.win_count / agentPnl.trade_count) * 100).toFixed(0) : "0";
              const pf = agentPnl && agentPnl.loss_count > 0 ? (agentPnl.win_count / agentPnl.loss_count).toFixed(1) : agentPnl && agentPnl.win_count > 0 ? "\u221E" : "0";
              return (
                <Link key={agent.id} href="/trading">
                  <Card className="hover:bg-white/[0.02] transition-colors cursor-pointer">
                    <div className="flex items-center gap-2 mb-3">
                      <span className="font-medium text-sm">{agent.name}</span>
                      <StatusBadge value={agent.status} />
                      <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: "var(--sidebar-active)", color: "var(--muted)" }}>{agent.symbol}</span>
                      <StatusBadge value={agent.agent_type} />
                    </div>
                    <div className="flex items-end justify-between">
                      <div>
                        <p className={"text-xl font-semibold " + (agent.total_pnl >= 0 ? "text-emerald-400" : "text-red-400")}>
                          {agent.total_pnl >= 0 ? "+" : ""}{fmt(agent.total_pnl)}
                        </p>
                        <div className="flex gap-4 mt-1 text-xs" style={{ color: "var(--muted)" }}>
                          <span>{wr}% win</span>
                          <span>{agent.trade_count} trades</span>
                          <span>PF: {pf}</span>
                        </div>
                      </div>
                      {sparkData.length >= 2 && <SparklineChart data={sparkData} width={100} height={36} />}
                    </div>
                  </Card>
                </Link>
              );
            })}
          </div>
        )}
      </div>

      {/* Section 6: Model Status */}
      {models.length > 0 && (
        <Link href="/models">
          <Card className="hover:bg-white/[0.02] transition-colors cursor-pointer">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <BrainCircuit size={20} style={{ color: "var(--accent)" }} />
                <span className="text-sm font-medium">ML Models</span>
                <span className="text-xs" style={{ color: "var(--muted)" }}>{models.length} models trained</span>
              </div>
              <div className="flex items-center gap-2">
                {avgGrade && <span className="text-xs" style={{ color: "var(--muted)" }}>Avg Grade:</span>}
                {avgGrade && <StatusBadge value={avgGrade} />}
                <span className="text-xs text-blue-400">View &rarr;</span>
              </div>
            </div>
          </Card>
        </Link>
      )}

      {/* Broker Modal */}
      <BrokerModal open={brokerModal} onClose={() => setBrokerModal(false)} onConnected={() => {
        api.get("/api/broker/status").then((r) => setBroker(r.data)).catch(() => {});
        api.get("/api/broker/account").then((r) => setAccount(r.data)).catch(() => {});
      }} />
    </div>
  );
}
