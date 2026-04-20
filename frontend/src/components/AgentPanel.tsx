"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import api from "@/lib/api";
import StatusBadge from "@/components/ui/StatusBadge";
import Card from "@/components/ui/Card";
import type { Agent, AgentLog, AgentTrade } from "@/types";
import { Play, Pause, Square, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { toSydneyTime } from "@/lib/timezone";
import { getErrorMessage } from "@/lib/errors";
import ConfirmDialog from "@/components/ui/ConfirmDialog";

function AgentCard({ agent, onAction }: { agent: Agent; onAction: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [logs, setLogs] = useState<AgentLog[]>([]);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
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
    <>
    <Card className="mb-2">
      {/*
        Collapsed row: a single clean line on mobile — status badge · name · P&L.
        Tap anywhere (except the controls) to expand. Secondary chips
        (agent_type, symbol, trade count) hide until expanded. Controls collapse
        to a compact cluster that wraps below on narrow viewports instead of
        overlapping the name.
      */}
      <div
        className="flex items-center gap-2 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
        role="button"
        aria-expanded={expanded}
      >
        {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <div className="flex-1 min-w-0 flex items-center gap-2">
          <StatusBadge value={agent.status} />
          <span className="font-medium text-sm truncate">{agent.name}</span>
          {/* Show agent_type + symbol only on sm+ or when expanded */}
          <span className={`hidden sm:inline text-xs uppercase tracking-wide ${expanded ? "" : "truncate"}`} style={{ color: "var(--muted)" }}>
            {agent.agent_type} · {agent.symbol}
          </span>
        </div>
        <span className={`text-sm font-medium tabular-nums ${pnlColor}`}>
          {agent.total_pnl >= 0 ? "+" : ""}{fmt(agent.total_pnl)}
        </span>
        <span className="hidden sm:inline text-xs" style={{ color: "var(--muted)" }}>
          {agent.trade_count}t
        </span>
        {/* Primary control — one icon on mobile, full set from sm+ */}
        <div className="flex gap-0.5" onClick={(e) => e.stopPropagation()}>
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
            <button onClick={() => handleAction("stop")} className="p-1.5 rounded hover:bg-white/10 hidden sm:inline-flex" title="Stop">
              <Square size={14} className="text-red-400" />
            </button>
          )}
          <button
            onClick={() => setDeleteConfirmOpen(true)}
            aria-label={`Delete agent ${agent.name}`}
            className="p-1.5 rounded hover:bg-white/10 hidden sm:inline-flex"
            title="Delete"
          >
            <Trash2 size={14} style={{ color: "var(--muted)" }} />
          </button>
        </div>
      </div>

      {/* Mobile-only: secondary chips + extra controls appear when expanded.
          Keeps the collapsed row to a single clean line. */}
      {expanded && (
        <div className="sm:hidden mt-2 flex items-center justify-between gap-2 text-xs" style={{ color: "var(--muted)" }}>
          <div className="flex items-center gap-2 min-w-0">
            <StatusBadge value={agent.agent_type} />
            <span className="truncate">{agent.symbol}</span>
            <span>·</span>
            <span>{agent.trade_count} trades</span>
          </div>
          <div className="flex gap-0.5" onClick={(e) => e.stopPropagation()}>
            {agent.status !== "stopped" && (
              <button onClick={() => handleAction("stop")} className="p-1.5 rounded hover:bg-white/10" title="Stop">
                <Square size={14} className="text-red-400" />
              </button>
            )}
            <button
              onClick={() => setDeleteConfirmOpen(true)}
              aria-label={`Delete agent ${agent.name}`}
              className="p-1.5 rounded hover:bg-white/10"
              title="Delete"
            >
              <Trash2 size={14} style={{ color: "var(--muted)" }} />
            </button>
          </div>
        </div>
      )}

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
                      {toSydneyTime(l.created_at + (l.created_at.includes("Z") || l.created_at.includes("+") ? "" : "Z"))}
                    </span>
                    <StatusBadge value={l.level} />
                    {/* max-h limits HTML error dumps (BTCUSD 2026-04-15 incident) */}
                    <span className="flex-1 min-w-0 break-all whitespace-pre-wrap max-h-32 overflow-y-auto block">
                      {l.message}
                    </span>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      )}
    </Card>
    <ConfirmDialog
      open={deleteConfirmOpen}
      onClose={() => setDeleteConfirmOpen(false)}
      onConfirm={handleDelete}
      title="Delete Agent"
      message={`Delete agent "${agent.name}"? This will stop the agent and remove all its logs and trades. This cannot be undone.`}
      confirmLabel="Delete Agent"
      variant="danger"
    />
    </>
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
