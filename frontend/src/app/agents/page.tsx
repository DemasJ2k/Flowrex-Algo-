"use client";

import { debugWarn } from "@/lib/debug";
import { useEffect, useState, useMemo, useRef } from "react";
import api from "@/lib/api";
import type { Agent, PnlSummaryItem } from "@/types";
import StatusBadge from "@/components/ui/StatusBadge";
import Card from "@/components/ui/Card";
import Glass from "@/components/ui/Glass";
import Sparkline from "@/components/ui/Sparkline";
import AnimatedNumber from "@/components/ui/AnimatedNumber";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import AgentWizard from "@/components/AgentWizard";
import AgentDetailModal from "@/components/AgentDetailModal";
import AgentConfigEditor from "@/components/AgentConfigEditor";
import { Plus, Play, Pause, Square, Trash2, Loader2, Copy, Search, SlidersHorizontal } from "lucide-react";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [pnl, setPnl] = useState<PnlSummaryItem[]>([]);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardDefaults, setWizardDefaults] = useState<Partial<Agent> | null>(null);
  const [detailAgent, setDetailAgent] = useState<Agent | null>(null);
  const [editAgent, setEditAgent] = useState<Agent | null>(null);
  const [deleteAgent, setDeleteAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [symbolFilter, setSymbolFilter] = useState("all");
  const [sortBy, setSortBy] = useState("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const [marketStatus, setMarketStatus] = useState<Record<string, { open: boolean; reason: string }>>({});

  const fetchData = () => {
    Promise.all([
      api.get("/api/agents/").then((r) => setAgents(r.data)).catch((e) => debugWarn("fetch failed:", e?.message)),
      api.get("/api/agents/pnl-summary").then((r) => setPnl(r.data)).catch((e) => debugWarn("fetch failed:", e?.message)),
    ]).finally(() => setLoading(false));
  };

  const fetchMarketStatus = () => {
    api.get("/api/market/status").then((r) => setMarketStatus(r.data)).catch(() => {});
  };

  useEffect(() => {
    fetchData();
    pollRef.current = setInterval(fetchData, 10000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  // Market hours change at most every few hours — poll separately every 5 min
  useEffect(() => {
    fetchMarketStatus();
    const t = setInterval(fetchMarketStatus, 300_000);
    return () => clearInterval(t);
  }, []);

  const handleAction = async (id: number, action: string) => {
    try {
      const res = await api.post(`/api/agents/${id}/${action}`);
      toast.success(`Agent ${res.data.status}`);
      fetchData();
    } catch (e) { toast.error(getErrorMessage(e)); }
  };

  const handleDelete = async () => {
    if (!deleteAgent) return;
    try {
      await api.delete(`/api/agents/${deleteAgent.id}`);
      toast.success(`Agent "${deleteAgent.name}" deleted`);
      fetchData();
    } catch (e) { toast.error(getErrorMessage(e)); }
    setDeleteAgent(null);
  };

  const handleClone = (agent: Agent) => {
    setWizardDefaults({ ...agent, name: `${agent.name} (Copy)` });
    setWizardOpen(true);
  };

  const handleBatchAction = async (action: string) => {
    const targets = agents.filter((a) => action === "start" ? a.status !== "running" : a.status === "running");
    // Await ALL requests before refreshing to avoid race condition
    await Promise.all(
      targets.map((a) => api.post(`/api/agents/${a.id}/${action}`).catch(() => null))
    );
    toast.success(`${action === "start" ? "Started" : "Stopped"} ${targets.length} agents`);
    fetchData();
  };

  const fmt = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // Unique symbols for filter
  const symbols = useMemo(() => [...new Set(agents.map((a) => a.symbol))], [agents]);

  // Filtered + sorted agents
  const displayed = useMemo(() => {
    let list = agents.filter((a) => {
      if (search && !a.name.toLowerCase().includes(search.toLowerCase()) && !a.symbol.toLowerCase().includes(search.toLowerCase())) return false;
      if (statusFilter !== "all" && a.status !== statusFilter) return false;
      if (symbolFilter !== "all" && a.symbol !== symbolFilter) return false;
      return true;
    });
    list.sort((a, b) => {
      let cmp = 0;
      if (sortBy === "name") cmp = a.name.localeCompare(b.name);
      else if (sortBy === "pnl") cmp = a.total_pnl - b.total_pnl;
      else if (sortBy === "trades") cmp = a.trade_count - b.trade_count;
      else if (sortBy === "status") cmp = a.status.localeCompare(b.status);
      else if (sortBy === "created") cmp = a.created_at.localeCompare(b.created_at);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return list;
  }, [agents, search, statusFilter, symbolFilter, sortBy, sortDir]);

  const runningCount = agents.filter((a) => a.status === "running").length;

  if (loading) {
    return <div className="flex items-center justify-center h-64"><Loader2 size={24} className="animate-spin" style={{ color: "var(--muted)" }} /></div>;
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start md:items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl md:text-3xl font-semibold tracking-tight text-gradient">Agents</h1>
          <p className="text-xs mt-0.5 tabular" style={{ color: "var(--muted)" }}>
            {agents.length} total &middot; {runningCount} running
          </p>
        </div>
        <div className="flex gap-2">
          {agents.length > 0 && (
            <>
              <button onClick={() => handleBatchAction("start")} className="px-3 py-1.5 text-xs rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>Start All</button>
              <button onClick={() => handleBatchAction("stop")} className="px-3 py-1.5 text-xs rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>Stop All</button>
            </>
          )}
          <button onClick={() => { setWizardDefaults(null); setWizardOpen(true); }} className="flex items-center gap-2 px-3 md:px-4 py-2 text-sm font-medium rounded-lg btn-gradient text-white">
            <Plus size={14} /> New Agent
          </button>
        </div>
      </div>

      {/* Filters */}
      {agents.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[180px] max-w-xs">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted)" }} />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search agents..."
              className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} />
          </div>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
            <option value="all">All Status</option>
            <option value="running">Running</option>
            <option value="stopped">Stopped</option>
            <option value="paused">Paused</option>
          </select>
          <select value={symbolFilter} onChange={(e) => setSymbolFilter(e.target.value)}
            className="px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
            <option value="all">All Symbols</option>
            {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={sortBy + "_" + sortDir} onChange={(e) => { const [k, d] = e.target.value.split("_"); setSortBy(k); setSortDir(d as "asc" | "desc"); }}
            className="px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
            <option value="name_asc">Name A-Z</option>
            <option value="name_desc">Name Z-A</option>
            <option value="pnl_desc">P&L High-Low</option>
            <option value="pnl_asc">P&L Low-High</option>
            <option value="trades_desc">Most Trades</option>
            <option value="status_asc">Status</option>
            <option value="created_desc">Newest</option>
            <option value="created_asc">Oldest</option>
          </select>
        </div>
      )}

      {/* Agent Grid */}
      {displayed.length === 0 && agents.length === 0 ? (
        <Glass padding="lg" className="text-center py-10">
          <p className="text-sm mb-3" style={{ color: "var(--muted)" }}>No agents yet</p>
          <button onClick={() => { setWizardDefaults(null); setWizardOpen(true); }} className="px-4 py-2 text-sm rounded-lg btn-gradient text-white">Create your first agent</button>
        </Glass>
      ) : displayed.length === 0 ? (
        <Glass padding="lg" className="text-center py-6">
          <p className="text-sm" style={{ color: "var(--muted)" }}>No agents match your filters</p>
        </Glass>
      ) : (
        <div className="grid gap-3">
          {displayed.map((a) => {
            const agentPnl = pnl.find((p) => p.agent_id === a.id);
            const wr = agentPnl && agentPnl.trade_count > 0 ? ((agentPnl.win_count / agentPnl.trade_count) * 100).toFixed(0) : "0";
            const pf = agentPnl && agentPnl.loss_count > 0 ? (agentPnl.win_count / agentPnl.loss_count).toFixed(1) : agentPnl && agentPnl.win_count > 0 ? "\u221E" : "\u2014";
            const statusColor = a.status === "running" ? "var(--status-live)"
              : a.status === "paused" ? "var(--status-paused)"
              : a.status === "error" ? "var(--pnl-down)"
              : "var(--status-stopped)";

            return (
              <Glass key={a.id} padding="md" hoverable>
                <div className="flex items-center gap-3 flex-wrap">
                  <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setDetailAgent(a)}>
                    {/* Row 1: status + name + badges */}
                    <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                      <span className={a.status === "running" ? "pulse-dot" : ""}
                            style={{ background: statusColor, width: 8, height: 8, borderRadius: "50%", display: "inline-block" }} />
                      <span className="font-medium text-sm hover:text-violet-300 truncate">{a.name}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded font-medium" style={{ background: "var(--sidebar-active)", color: "var(--muted)" }}>{a.symbol}</span>
                      <StatusBadge value={a.agent_type} />
                      <StatusBadge value={a.mode} />
                      {marketStatus[a.symbol] && (
                        <span className="text-[9px] uppercase tracking-wider font-medium"
                              style={{ color: marketStatus[a.symbol].open ? "var(--status-live)" : "var(--muted)" }}
                              title={marketStatus[a.symbol].reason}>
                          {marketStatus[a.symbol].open ? "mkt open" : "mkt closed"}
                        </span>
                      )}
                    </div>
                    {/* Row 2: Metrics */}
                    <div className="flex items-center gap-4 text-xs tabular" style={{ color: "var(--muted)" }}>
                      <span className={a.total_pnl >= 0 ? "text-emerald-400 font-semibold" : "text-red-400 font-semibold"}>
                        {a.total_pnl >= 0 ? "+" : ""}
                        <AnimatedNumber value={a.total_pnl} format={(v) => fmt(Math.abs(v))} />
                      </span>
                      <span>{wr}% win</span>
                      <span>{a.trade_count} trades</span>
                      <span>PF {pf}</span>
                      <span className="hidden md:inline">{a.broker_name}</span>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-0.5 flex-shrink-0">
                    {a.status !== "running" && (
                      <button onClick={() => handleAction(a.id, "start")} className="p-2 rounded hover:bg-white/10" title="Start"><Play size={14} className="text-emerald-400" /></button>
                    )}
                    {a.status === "running" && (
                      <button onClick={() => handleAction(a.id, "pause")} className="p-2 rounded hover:bg-white/10" title="Pause"><Pause size={14} className="text-amber-400" /></button>
                    )}
                    {a.status !== "stopped" && (
                      <button onClick={() => handleAction(a.id, "stop")} className="p-2 rounded hover:bg-white/10" title="Stop"><Square size={14} className="text-red-400" /></button>
                    )}
                    <button onClick={() => handleClone(a)} className="p-2 rounded hover:bg-white/10" title="Clone"><Copy size={14} style={{ color: "var(--muted)" }} /></button>
                    <button onClick={() => setEditAgent(a)} className="p-2 rounded hover:bg-white/10" title="Edit"><SlidersHorizontal size={14} style={{ color: "var(--muted)" }} /></button>
                    <button onClick={() => setDeleteAgent(a)} className="p-2 rounded hover:bg-white/10" title="Delete"><Trash2 size={14} style={{ color: "var(--muted)" }} /></button>
                  </div>
                </div>
              </Glass>
            );
          })}
        </div>
      )}

      {/* Modals */}
      <AgentWizard open={wizardOpen} onClose={() => { setWizardOpen(false); setWizardDefaults(null); }} onCreated={fetchData} />
      <AgentDetailModal agent={detailAgent} open={detailAgent !== null} onClose={() => setDetailAgent(null)} onEdit={() => { setEditAgent(detailAgent); setDetailAgent(null); }} />
      <AgentConfigEditor agent={editAgent} open={editAgent !== null} onClose={() => setEditAgent(null)} onSaved={fetchData} />
      <ConfirmDialog open={deleteAgent !== null} onClose={() => setDeleteAgent(null)} onConfirm={handleDelete}
        title="Delete Agent" message={`Delete "${deleteAgent?.name}"? This action cannot be undone.`} confirmLabel="Delete" variant="danger" />
    </div>
  );
}
