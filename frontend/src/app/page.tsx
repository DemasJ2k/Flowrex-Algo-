"use client";

import { debugWarn } from "@/lib/debug";
import { useEffect, useState, useMemo, useCallback, useRef } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import LandingPage from "@/components/LandingPage";
import Glass from "@/components/ui/Glass";
import StatRing from "@/components/ui/StatRing";
import AnimatedNumber from "@/components/ui/AnimatedNumber";
import Sparkline from "@/components/ui/Sparkline";
import StatusBadge from "@/components/ui/StatusBadge";
import EquityCurveChart from "@/components/EquityCurveChart";
import BrokerModal from "@/components/BrokerModal";
import api from "@/lib/api";
import type {
  AccountInfo, PnlSummaryItem, Agent, AgentTrade,
  EngineLog, BrokerStatus, LivePosition, MLModel,
} from "@/types";
import {
  Bot, LineChart, Plus, Plug, FlaskConical, BrainCircuit,
  TrendingUp, Activity, Zap, ArrowUpRight,
} from "lucide-react";

function timeAgo(dateStr: string): string {
  const diff = (Date.now() - new Date(dateStr).getTime()) / 1000;
  if (diff < 60) return Math.floor(diff) + "s";
  if (diff < 3600) return Math.floor(diff / 60) + "m";
  if (diff < 86400) return Math.floor(diff / 3600) + "h";
  return Math.floor(diff / 86400) + "d";
}

export default function HomePage() {
  const [isAuthed, setIsAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    setIsAuthed(!!localStorage.getItem("access_token"));
  }, []);

  if (isAuthed === null) return null;
  if (!isAuthed) return <LandingPage />;
  return <DashboardView />;
}

interface MarketStatus {
  open: boolean;
  reason: string;
  asset_class: string;
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
  const [marketStatus, setMarketStatus] = useState<Record<string, MarketStatus>>({});
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);
  const [brokerModal, setBrokerModal] = useState(false);

  const fetchDashboard = useCallback(async () => {
    try {
      const [brk, acct, pos, ag, pnlRes, trRes, logRes, mdl] = await Promise.all([
        api.get("/api/broker/status").catch(() => null),
        api.get("/api/broker/account").catch(() => null),
        api.get("/api/broker/positions").catch(() => null),
        api.get("/api/agents/").catch(() => null),
        api.get("/api/agents/pnl-summary").catch(() => null),
        api.get("/api/agents/all-trades?limit=100").catch(() => null),
        api.get("/api/agents/engine-logs?limit=8").catch(() => null),
        api.get("/api/ml/models").catch(() => null),
      ]);
      if (brk?.data) setBroker(brk.data);
      if (acct?.data) setAccount(acct.data);
      if (pos?.data) setPositions(pos.data);
      if (ag?.data) setAgents(ag.data);
      if (pnlRes?.data) setPnl(pnlRes.data);
      if (trRes?.data) setTrades(trRes.data);
      if (logRes?.data) setLogs(logRes.data);
      if (mdl?.data) setModels(mdl.data);
      if (brk?.data || ag?.data) setFetchError(false);
      else setFetchError(true);
    } catch {
      setFetchError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchMarketStatus = useCallback(() => {
    api.get("/api/market/status")
      .then((r) => setMarketStatus(r.data))
      .catch((e) => debugWarn("market status:", e?.message));
  }, []);

  useEffect(() => { fetchDashboard(); }, [fetchDashboard]);
  useEffect(() => {
    fetchMarketStatus();
    const t = setInterval(fetchMarketStatus, 300_000);
    return () => clearInterval(t);
  }, [fetchMarketStatus]);

  const dashPollRef = useRef<ReturnType<typeof setInterval>>(undefined);
  useEffect(() => {
    dashPollRef.current = setInterval(fetchDashboard, 30000);
    return () => { if (dashPollRef.current) clearInterval(dashPollRef.current); };
  }, [fetchDashboard]);

  const fmt = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const totalPnl = useMemo(() => pnl.reduce((s, p) => s + p.total_pnl, 0), [pnl]);
  const totalTrades = useMemo(() => pnl.reduce((s, p) => s + p.trade_count, 0), [pnl]);
  const totalWins = useMemo(() => pnl.reduce((s, p) => s + p.win_count, 0), [pnl]);
  const winRate = totalTrades > 0 ? (totalWins / totalTrades) : 0;

  const todayPnl = useMemo(() => {
    const now = new Date();
    const todayUtc = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}-${String(now.getUTCDate()).padStart(2, "0")}`;
    return trades
      .filter((t) => t.exit_time?.startsWith(todayUtc) && t.status === "closed")
      .reduce((s, t) => s + (t.broker_pnl ?? t.pnl ?? 0), 0);
  }, [trades]);

  const equityCurve = useMemo(() => {
    const closed = trades
      .filter((t) => t.status === "closed" && t.exit_time)
      .sort((a, b) => new Date(a.exit_time!).getTime() - new Date(b.exit_time!).getTime());
    if (closed.length === 0) return [];
    const points: { time: number; value: number }[] = [
      { time: Math.floor(new Date(closed[0].exit_time!).getTime() / 1000) - 1, value: 0 },
    ];
    let cum = 0;
    for (const t of closed) {
      cum += t.broker_pnl ?? t.pnl ?? 0;
      points.push({
        time: Math.floor(new Date(t.exit_time!).getTime() / 1000),
        value: Math.round(cum * 100) / 100,
      });
    }
    return points;
  }, [trades]);

  const activeAgents = agents.filter((a) => a.status === "running").length;

  // Progress ring for today's target (default 2%). Could be user-configurable later.
  const dailyTarget = account ? account.balance * 0.02 : 200;
  const dailyProgress = Math.max(0, Math.min(1, todayPnl / dailyTarget));

  if (loading) {
    return <DashboardSkeleton />;
  }

  return (
    <div className="space-y-4 md:space-y-6">
      {/* Error banner */}
      {fetchError && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="px-4 py-3 rounded-lg border border-amber-500/30 bg-amber-500/10 text-sm text-amber-400 flex items-center justify-between"
        >
          <span>Some data failed to load. Check your broker connection.</span>
          <button onClick={fetchDashboard} className="text-xs px-3 py-1 rounded border border-amber-500/30 hover:bg-amber-500/20">
            Retry
          </button>
        </motion.div>
      )}

      {/* Header */}
      <div className="flex items-start md:items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl md:text-3xl font-semibold tracking-tight text-gradient">Dashboard</h1>
          <p className="text-xs mt-0.5" style={{ color: "var(--muted)" }}>
            Live snapshot &middot; auto-refreshes every 30s
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            href="/agents"
            className="flex items-center gap-2 px-3 md:px-4 py-2 text-xs md:text-sm font-medium rounded-lg btn-gradient text-white"
          >
            <Plus size={14} /> New Agent
          </Link>
        </div>
      </div>

      {/* Broker banner */}
      {!broker.connected ? (
        <Glass padding="sm" className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-amber-400 text-sm">
            <Plug size={16} />
            <span>No broker connected</span>
          </div>
          <button
            onClick={() => setBrokerModal(true)}
            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/30"
          >
            Connect
          </button>
        </Glass>
      ) : (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-emerald-400 text-xs md:text-sm"
             style={{ background: "rgba(16, 185, 129, 0.08)", border: "1px solid rgba(16, 185, 129, 0.25)" }}>
          <span className="pulse-dot" style={{ background: "var(--status-live)" }} />
          <span className="font-medium">{broker.broker}</span>
          {account && (
            <span className="tabular" style={{ color: "var(--muted)" }}>
              · {fmt(account.balance)} {account.currency}
              {account.margin_used > 0 && ` · ${fmt(account.margin_used)} margin`}
            </span>
          )}
        </div>
      )}

      {/* ── Hero: today's progress ring + key stats ────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Progress ring (column 1) */}
        <Glass padding="lg" className="flex flex-col items-center justify-center text-center grid-bg relative overflow-hidden">
          <div className="relative z-10">
            <StatRing
              value={Math.abs(dailyProgress)}
              size={160}
              stroke={12}
              centerText={`${todayPnl >= 0 ? "+" : ""}${fmt(todayPnl)}`}
              subText={`target ${fmt(dailyTarget)}`}
              color={todayPnl >= 0 ? undefined : "var(--pnl-down)"}
              showPercent={false}
            />
            <p className="mt-3 text-xs uppercase tracking-wider" style={{ color: "var(--muted)" }}>
              Today&apos;s P&amp;L
            </p>
          </div>
        </Glass>

        {/* Stats (column 2-3) */}
        <div className="lg:col-span-2 grid grid-cols-2 md:grid-cols-3 gap-3">
          <StatTile
            label="Balance"
            value={account ? fmt(account.balance) : "—"}
            sub={account?.currency || ""}
            icon={<Activity size={14} />}
          />
          <StatTile
            label="Total P&L"
            value={totalPnl !== 0 ? (totalPnl >= 0 ? "+" : "") + fmt(totalPnl) : "—"}
            sub={`${totalTrades} trades`}
            color={totalPnl > 0 ? "up" : totalPnl < 0 ? "down" : undefined}
          />
          <StatTile
            label="Win Rate"
            value={totalTrades > 0 ? (winRate * 100).toFixed(1) + "%" : "—"}
            sub={`${totalWins}W · ${totalTrades - totalWins}L`}
          />
          <StatTile
            label="Open"
            value={positions.length}
            sub={`${activeAgents} agents live`}
          />
          <StatTile
            label="Equity"
            value={account ? fmt(account.equity) : "—"}
            sub={account?.currency || ""}
          />
          <StatTile
            label="Models"
            value={models.length}
            sub={models.filter((m) => m.grade === "A").length + " Grade A"}
            icon={<BrainCircuit size={14} />}
          />
        </div>
      </div>

      {/* ── Market Pulse ─────────────────────────────────────────────── */}
      {Object.keys(marketStatus).length > 0 && (
        <Glass padding="md">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium flex items-center gap-2">
              <Zap size={14} className="text-violet-400" /> Market Pulse
            </h2>
            <span className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
              updated every 5m
            </span>
          </div>
          <div className="grid grid-cols-4 md:grid-cols-8 gap-2">
            {Object.entries(marketStatus).map(([sym, m]) => (
              <div
                key={sym}
                className="px-2 py-2 rounded-lg flex flex-col items-center gap-1"
                style={{
                  background: m.open ? "rgba(16,185,129,0.08)" : "rgba(107,114,128,0.08)",
                  border: `1px solid ${m.open ? "rgba(16,185,129,0.25)" : "var(--border)"}`,
                }}
                title={m.reason}
              >
                <span
                  className={m.open ? "pulse-dot" : ""}
                  style={{
                    background: m.open ? "var(--status-live)" : "var(--status-stopped)",
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    display: "block",
                  }}
                />
                <span className="text-xs font-medium">{sym}</span>
                <span className="text-[9px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
                  {m.open ? "open" : "closed"}
                </span>
              </div>
            ))}
          </div>
        </Glass>
      )}

      {/* ── Equity curve ──────────────────────────────────────────────── */}
      <Glass padding="md">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium flex items-center gap-2">
            <TrendingUp size={14} style={{ color: "var(--muted)" }} /> Equity Curve
          </h2>
          {equityCurve.length > 0 && (
            <span
              className={"text-sm font-semibold tabular " + (totalPnl >= 0 ? "text-emerald-400" : "text-red-400")}
            >
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
      </Glass>

      {/* ── Activity + Quick Actions ──────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Glass padding="md" className="lg:col-span-3">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium flex items-center gap-2">
              <Activity size={14} style={{ color: "var(--muted)" }} /> Recent Activity
            </h2>
            <Link href="/trading" className="text-xs text-violet-400 hover:text-violet-300">
              View all
            </Link>
          </div>
          {logs.length === 0 ? (
            <p className="text-sm py-6 text-center" style={{ color: "var(--muted)" }}>
              No activity yet
            </p>
          ) : (
            <div className="space-y-1">
              {logs.map((log) => (
                <div
                  key={log.id}
                  className="flex items-start gap-2 text-xs py-1.5 border-b last:border-0"
                  style={{ borderColor: "var(--border)" }}
                >
                  <span className="tabular flex-shrink-0 w-10 text-right" style={{ color: "var(--muted)" }}>
                    {timeAgo(log.created_at)}
                  </span>
                  <StatusBadge value={log.level} />
                  <span className="truncate flex-1">{log.message}</span>
                </div>
              ))}
            </div>
          )}
        </Glass>

        <Glass padding="md" className="lg:col-span-2 grid-bg relative overflow-hidden">
          <h2 className="text-sm font-medium mb-3">Quick Actions</h2>
          <div className="space-y-2 relative z-10">
            <Link href="/trading" className="flex items-center justify-between w-full px-3 py-2.5 text-sm rounded-lg btn-gradient text-white font-medium">
              <span className="flex items-center gap-2"><LineChart size={16} /> Trading Terminal</span>
              <ArrowUpRight size={14} />
            </Link>
            <Link href="/agents" className="flex items-center justify-between w-full px-3 py-2.5 text-sm rounded-lg border hover:bg-white/5 transition-colors" style={{ borderColor: "var(--border)" }}>
              <span className="flex items-center gap-2"><Plus size={16} /> New Agent</span>
              <ArrowUpRight size={14} style={{ color: "var(--muted)" }} />
            </Link>
            <Link href="/backtest" className="flex items-center justify-between w-full px-3 py-2.5 text-sm rounded-lg border hover:bg-white/5 transition-colors" style={{ borderColor: "var(--border)" }}>
              <span className="flex items-center gap-2"><FlaskConical size={16} /> Run Backtest</span>
              <ArrowUpRight size={14} style={{ color: "var(--muted)" }} />
            </Link>
            <Link href="/models" className="flex items-center justify-between w-full px-3 py-2.5 text-sm rounded-lg border hover:bg-white/5 transition-colors" style={{ borderColor: "var(--border)" }}>
              <span className="flex items-center gap-2"><BrainCircuit size={16} /> ML Models</span>
              <ArrowUpRight size={14} style={{ color: "var(--muted)" }} />
            </Link>
          </div>
        </Glass>
      </div>

      {/* ── Agent Performance ─────────────────────────────────────────── */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium flex items-center gap-2">
            <Bot size={14} style={{ color: "var(--muted)" }} /> Agents
          </h2>
          <Link href="/agents" className="text-xs text-violet-400 hover:text-violet-300">
            Manage all
          </Link>
        </div>
        {agents.length === 0 ? (
          <Glass padding="lg" className="text-center">
            <Bot size={32} className="mx-auto mb-2 opacity-30" />
            <p className="text-sm" style={{ color: "var(--muted)" }}>
              No agents yet.{" "}
              <Link href="/agents" className="text-violet-400 hover:text-violet-300">
                Create one
              </Link>
            </p>
          </Glass>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {agents.map((agent, i) => {
              const agentPnl = pnl.find((p) => p.agent_id === agent.id);
              const agentTrades = trades.filter((t) => t.agent_id === agent.id);
              const sparkData = agentTrades
                .filter((t) => t.status === "closed")
                .slice(-20)
                .map((t) => t.broker_pnl ?? t.pnl ?? 0);
              const wr = agentPnl && agentPnl.trade_count > 0
                ? ((agentPnl.win_count / agentPnl.trade_count) * 100).toFixed(0)
                : "0";
              const pf = agentPnl && agentPnl.loss_count > 0
                ? (agentPnl.win_count / agentPnl.loss_count).toFixed(1)
                : agentPnl && agentPnl.win_count > 0 ? "\u221E" : "0";
              const mkt = marketStatus[agent.symbol];

              return (
                <motion.div
                  key={agent.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.04 }}
                >
                  <Link href="/agents">
                    <Glass hoverable padding="md">
                      <div className="flex items-center gap-2 mb-3 flex-wrap">
                        <span className="pulse-dot"
                          style={{
                            background: agent.status === "running" ? "var(--status-live)"
                              : agent.status === "paused" ? "var(--status-paused)"
                              : "var(--status-stopped)",
                          }} />
                        <span className="font-medium text-sm">{agent.name}</span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                          style={{ background: "var(--sidebar-active)", color: "var(--muted)" }}>
                          {agent.symbol}
                        </span>
                        <StatusBadge value={agent.agent_type} />
                        {mkt && (
                          <span className="text-[9px] uppercase tracking-wider"
                            style={{ color: mkt.open ? "var(--status-live)" : "var(--muted)" }}>
                            {mkt.open ? "mkt open" : "mkt closed"}
                          </span>
                        )}
                      </div>
                      <div className="flex items-end justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <p className={"text-2xl font-semibold tabular " + (agent.total_pnl >= 0 ? "text-emerald-400" : "text-red-400")}>
                            {agent.total_pnl >= 0 ? "+" : ""}
                            <AnimatedNumber
                              value={agent.total_pnl}
                              format={(v) => fmt(Math.abs(v))}
                            />
                          </p>
                          <div className="flex gap-3 mt-1 text-[11px] tabular" style={{ color: "var(--muted)" }}>
                            <span>{wr}% win</span>
                            <span>{agent.trade_count} trades</span>
                            <span>PF {pf}</span>
                          </div>
                        </div>
                        {sparkData.length >= 2 && (
                          <Sparkline data={sparkData} width={96} height={36} />
                        )}
                      </div>
                    </Glass>
                  </Link>
                </motion.div>
              );
            })}
          </div>
        )}
      </div>

      <BrokerModal open={brokerModal} onClose={() => setBrokerModal(false)} onConnected={() => {
        api.get("/api/broker/status").then((r) => setBroker(r.data)).catch((e) => debugWarn("fetch failed:", e?.message));
        api.get("/api/broker/account").then((r) => setAccount(r.data)).catch((e) => debugWarn("fetch failed:", e?.message));
      }} />
    </div>
  );
}

// ── StatTile — compact metric card for the hero grid ──────────────────
function StatTile({
  label, value, sub, color, icon,
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: "up" | "down";
  icon?: React.ReactNode;
}) {
  const valueClass =
    color === "up" ? "text-emerald-400"
    : color === "down" ? "text-red-400"
    : "";
  return (
    <Glass padding="md" className="flex flex-col justify-between min-h-[88px]">
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
          {label}
        </span>
        {icon && <span style={{ color: "var(--muted)" }}>{icon}</span>}
      </div>
      <div>
        <div className={`text-xl md:text-2xl font-semibold tabular ${valueClass}`}
             style={{ letterSpacing: "-0.02em" }}>
          {value}
        </div>
        {sub && (
          <div className="text-[10px] mt-0.5 tabular" style={{ color: "var(--muted)" }}>
            {sub}
          </div>
        )}
      </div>
    </Glass>
  );
}

// ── Loading skeleton ──────────────────────────────────────────────────
function DashboardSkeleton() {
  return (
    <div className="space-y-4 md:space-y-6">
      <div className="flex items-center justify-between">
        <div className="skeleton h-8 w-48" />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="skeleton h-[240px] rounded-xl" />
        <div className="lg:col-span-2 grid grid-cols-2 md:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="skeleton h-[88px] rounded-xl" />
          ))}
        </div>
      </div>
      <div className="skeleton h-[220px] rounded-xl" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="skeleton h-[120px] rounded-xl" />
        <div className="skeleton h-[120px] rounded-xl" />
      </div>
    </div>
  );
}
