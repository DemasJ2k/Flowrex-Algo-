"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import api from "@/lib/api";
import StatusBadge from "@/components/ui/StatusBadge";
import Card from "@/components/ui/Card";
import type { Agent, AgentLog, AgentTrade } from "@/types";
import { Play, Pause, Square, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";

function AgentCard({ agent, onAction }: { agent: Agent; onAction: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [logs, setLogs] = useState<AgentLog[]>([]);
  const [trades, setTrades] = useState<AgentTrade[]>([]);
  const [subTab, setSubTab] = useState<"trades" | "logs">("trades");
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);

  const fetchDetails = useCallback(async () => {
    try {
      const [logRes, tradeRes] = await Promise.all([
        api.get(`/api/agents/${agent.id}/logs?limit=50`),
        api.get(`/api/agents/${agent.id}/trades?limit=50`),
      ]);
      setLogs(logRes.data);
      setTrades(tradeRes.data);
    } catch {}
  }, [agent.id]);

  useEffect(() => {
    if (expanded) {
      fetchDetails();
      pollRef.current = setInterval(fetchDetails, 5000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [expanded, fetchDetails]);

  const handleAction = async (action: string) => {
    try {
      const res = await api.post(`/api/agents/${agent.id}/${action}`);
      toast.success(`Agent ${agent.name}: ${res.data.status}`);
      onAction();
    } catch (e) {
      toast.error(`Failed to ${action} agent: ${getErrorMessage(e)}`);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Delete agent "${agent.name}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/api/agents/${agent.id}`);
      toast.success(`Agent "${agent.name}" deleted`);
      onAction();
    } catch (e) {
      toast.error(`Failed to delete agent: ${getErrorMessage(e)}`);
    }
  };

  const pnlColor = agent.total_pnl >= 0 ? "text-emerald-400" : "text-red-400";
  const fmt = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <Card className="mb-2">
      {/* Header */}
      <div className="flex items-center gap-3 cursor-pointer" onClick={() => setExpanded(!expanded)}>
        {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm truncate">{agent.name}</span>
            <StatusBadge value={agent.status} />
            <StatusBadge value={agent.agent_type} />
            <span className="text-xs" style={{ color: "var(--muted)" }}>{agent.symbol}</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className={`text-sm font-medium ${pnlColor}`}>
            {agent.total_pnl >= 0 ? "+" : ""}{fmt(agent.total_pnl)}
          </span>
          <span className="text-xs" style={{ color: "var(--muted)" }}>{agent.trade_count} trades</span>
        </div>
        {/* Controls */}
        <div className="flex gap-1 ml-2" onClick={(e) => e.stopPropagation()}>
          {agent.status !== "running" && (
            <button onClick={() => handleAction("start")} className="p-1.5 rounded hover:bg-white/10" title="Start">
              <Play size={14} className="text-emerald-400" />
            </button>
          )}
          {agent.status === "running" && (
            <button onClick={() => handleAction("pause")} className="p-1.5 rounded hover:bg-white/10" title="Pause">
              <Pause size={14} className="text-amber-400" />
            </button>
          )}
          {agent.status !== "stopped" && (
            <button onClick={() => handleAction("stop")} className="p-1.5 rounded hover:bg-white/10" title="Stop">
              <Square size={14} className="text-red-400" />
            </button>
          )}
          <button onClick={handleDelete} className="p-1.5 rounded hover:bg-white/10" title="Delete">
            <Trash2 size={14} style={{ color: "var(--muted)" }} />
          </button>
        </div>
      </div>

      {/* Expanded Content */}
      {expanded && (
        <div className="mt-3 pt-3 border-t" style={{ borderColor: "var(--border)" }}>
          {/* Sub-tabs */}
          <div className="flex gap-4 mb-3">
            {(["trades", "logs"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setSubTab(t)}
                className={`text-xs font-medium pb-1 border-b-2 transition-colors ${
                  subTab === t ? "border-blue-500 text-white" : "border-transparent"
                }`}
                style={{ color: subTab === t ? undefined : "var(--muted)" }}
              >
                {t === "trades" ? `Trades (${trades.length})` : `Logs (${logs.length})`}
              </button>
            ))}
          </div>

          {subTab === "trades" && (
            <div className="max-h-48 overflow-y-auto text-xs space-y-1">
              {trades.length === 0 ? (
                <p style={{ color: "var(--muted)" }}>No trades yet</p>
              ) : (
                trades.map((t) => (
                  <div key={t.id} className="flex items-center gap-3 py-1">
                    <StatusBadge value={t.direction} />
                    <span>{t.symbol}</span>
                    <span style={{ color: "var(--muted)" }}>{t.lot_size} lots</span>
                    <span style={{ color: "var(--muted)" }}>{t.entry_price} → {t.exit_price ?? "open"}</span>
                    <span className="ml-auto">
                      {t.pnl != null && (
                        <span className={(t.pnl ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}>
                          {(t.pnl ?? 0) >= 0 ? "+" : ""}{fmt(t.pnl ?? 0)}
                        </span>
                      )}
                    </span>
                    {t.exit_reason && <StatusBadge value={t.exit_reason} />}
                  </div>
                ))
              )}
            </div>
          )}

          {subTab === "logs" && (
            <div className="max-h-48 overflow-y-auto text-xs space-y-1 font-mono">
              {logs.length === 0 ? (
                <p style={{ color: "var(--muted)" }}>No logs yet</p>
              ) : (
                logs.map((l) => (
                  <div key={l.id} className="flex items-start gap-2 py-0.5">
                    <span style={{ color: "var(--muted)" }} className="flex-shrink-0">
                      {new Date(l.created_at + (l.created_at.includes("Z") || l.created_at.includes("+") ? "" : "Z")).toLocaleTimeString()}
                    </span>
                    <StatusBadge value={l.level} />
                    <span className="break-all">{l.message}</span>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export default function AgentPanel({ onRefresh }: { onRefresh?: () => void }) {
  const [agents, setAgents] = useState<Agent[]>([]);

  const fetchAgents = useCallback(async () => {
    try {
      const res = await api.get("/api/agents/");
      setAgents(res.data);
    } catch {}
  }, []);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  const handleAction = () => {
    fetchAgents();
    onRefresh?.();
  };

  if (agents.length === 0) {
    return <p className="text-sm py-4" style={{ color: "var(--muted)" }}>No agents. Create one to get started.</p>;
  }

  return (
    <div>
      {agents.map((a) => (
        <AgentCard key={a.id} agent={a} onAction={handleAction} />
      ))}
    </div>
  );
}
