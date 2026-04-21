"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import api from "@/lib/api";
import Glass from "@/components/ui/Glass";
import Modal from "@/components/ui/Modal";
import StatusBadge from "@/components/ui/StatusBadge";
import { debugWarn } from "@/lib/debug";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import {
  Activity, BarChart3, Bot, BrainCircuit, ChevronDown, ChevronUp,
  Clock, History, Loader2, Play, RefreshCw, Sparkles, TrendingUp, X, Zap,
} from "lucide-react";

// ── Types from /api/ml/symbols ───────────────────────────────────────────

interface ModelVariant {
  pipeline: "potential" | "flowrex" | "scout" | string;
  model_type: string; // xgboost / lightgbm / catboost
  grade: string;
  sharpe: number;
  win_rate: number;
  max_drawdown: number;
  profit_factor: number;
  total_trades: number;
  trained_at: string;
  oos_start: string;
  feature_count: number;
  pipeline_version: string;
  file: string;
  // Present only for synthetic pipelines (e.g. "scout") that reuse another
  // pipeline's joblib. UI surfaces a "reuses X" badge when set.
  proxy_for?: string;
}

interface SymbolRow {
  symbol: string;
  asset_class: string;
  models: ModelVariant[];
  live_14d: {
    trades: number;
    win_rate: number;
    total_pnl: number;
    avg_win: number;
    avg_loss: number;
  };
  agents: {
    id: number; name: string; agent_type: string;
    status: string; broker: string;
  }[];
  last_retrain: {
    id: number; status: string;
    started_at: string | null; finished_at: string | null;
    old_grade: string | null; new_grade: string | null;
    old_sharpe: number | null; new_sharpe: number | null;
    swapped: boolean; error: string | null;
  } | null;
}

interface RetrainRun {
  id: number; symbol: string; triggered_by: string; started_at: string;
  finished_at: string | null; status: string;
  old_grade: string | null; new_grade: string | null;
  old_sharpe: number | null; new_sharpe: number | null;
  swapped: boolean;
}

interface RetrainSchedule { enabled: boolean; cron_expression: string; next_run: string | null; }

// ── Small UI helpers ─────────────────────────────────────────────────────

const GRADE_COLOR: Record<string, string> = {
  A: "text-emerald-400 border-emerald-500/40 bg-emerald-500/10",
  B: "text-blue-400 border-blue-500/40 bg-blue-500/10",
  C: "text-amber-400 border-amber-500/40 bg-amber-500/10",
  D: "text-orange-400 border-orange-500/40 bg-orange-500/10",
  F: "text-red-400 border-red-500/40 bg-red-500/10",
};

function GradeChip({ grade, size = "md" }: { grade: string; size?: "sm" | "md" }) {
  const cls = GRADE_COLOR[grade] || GRADE_COLOR.F;
  const sz = size === "sm" ? "w-6 h-6 text-xs" : "w-8 h-8 text-base";
  return (
    <span className={`inline-flex items-center justify-center rounded-lg font-bold border ${sz} ${cls}`}>
      {grade || "?"}
    </span>
  );
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "running" ? "bg-emerald-400 animate-pulse" :
    status === "paused"  ? "bg-amber-400" :
    "bg-slate-600";
  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} />;
}

function fmtMoney(v: number): string {
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return new Date(s).toISOString().slice(0, 10);
}

// ── Page ─────────────────────────────────────────────────────────────────

export default function ModelsPage() {
  const [rows, setRows] = useState<SymbolRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // Training / retrain
  const [retraining, setRetraining] = useState(false);
  const [retrainProgress, setRetrainProgress] = useState("");
  const [retrainSymbol, setRetrainSymbol] = useState<string | null>(null);
  const [retrainHistory, setRetrainHistory] = useState<RetrainRun[]>([]);
  const [schedule, setSchedule] = useState<RetrainSchedule | null>(null);

  // Modals
  const [wizardFor, setWizardFor] = useState<SymbolRow | null>(null);
  const [aiFor, setAiFor] = useState<{ row: SymbolRow; pipeline: string } | null>(null);

  // ── Data fetchers ─────────────────────────────────────────────────────

  const fetchRows = useCallback(async () => {
    try {
      const r = await api.get("/api/ml/symbols");
      setRows(r.data || []);
    } catch (e) {
      debugWarn("symbols fetch failed:", (e as Error)?.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchRetrainData = useCallback(async () => {
    api.get("/api/ml/retrain/history?limit=15")
      .then((r) => setRetrainHistory(r.data || []))
      .catch(() => {});
    api.get("/api/ml/retrain/schedule")
      .then((r) => setSchedule(r.data))
      .catch(() => {});
  }, []);

  useEffect(() => { fetchRows(); fetchRetrainData(); }, [fetchRows, fetchRetrainData]);

  // Poll retrain status while training is active
  useEffect(() => {
    if (!retraining) return;
    const iv = setInterval(async () => {
      try {
        const r = await api.get("/api/ml/retrain/status");
        setRetrainProgress(r.data?.progress || "");
        if (!r.data?.active) {
          setRetraining(false);
          setRetrainSymbol(null);
          fetchRows();
          fetchRetrainData();
          toast.success("Retrain complete — check the symbol card");
        }
      } catch { /* keep polling */ }
    }, 3000);
    return () => clearInterval(iv);
  }, [retraining, fetchRows, fetchRetrainData]);

  // ── Actions ───────────────────────────────────────────────────────────

  const startRetrain = async (symbol: string, opts: {
    pipeline: "flowrex_v2" | "potential";
    train_months: number;
    n_trials: number;
    refresh_dukascopy: boolean;
  }) => {
    try {
      const res = await api.post("/api/ml/retrain", { symbol, ...opts });
      if (res.data?.status === "started") {
        setRetraining(true);
        setRetrainSymbol(symbol);
        setRetrainProgress("Starting...");
        setWizardFor(null);
        toast.success(`Retraining ${symbol} (${opts.pipeline}, ${opts.train_months}m)`);
      } else {
        toast.error(res.data?.message || "Busy");
      }
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };

  const toggleSchedule = async () => {
    if (!schedule) return;
    try {
      const res = await api.post("/api/ml/retrain/schedule", {
        enabled: !schedule.enabled,
        cron_expression: schedule.cron_expression,
      });
      setSchedule(res.data);
      toast.success(schedule.enabled ? "Schedule disabled" : "Schedule enabled");
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };

  // ── Derived ───────────────────────────────────────────────────────────

  const totalLivePnl = useMemo(
    () => rows.reduce((s, r) => s + (r.live_14d?.total_pnl || 0), 0),
    [rows],
  );
  const activeModelCount = useMemo(
    () => rows.reduce((s, r) => s + r.models.length, 0), [rows],
  );

  // ── Render ────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin text-violet-400" />
      </div>
    );
  }

  return (
    <div className="space-y-5 max-w-7xl">
      {/* ── Header ──────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl md:text-3xl font-semibold tracking-tight text-gradient flex items-center gap-2">
            <BrainCircuit size={24} className="text-violet-400" />
            ML Models
          </h1>
          <p className="text-xs mt-0.5" style={{ color: "var(--muted)" }}>
            {rows.length} symbols · {activeModelCount} deployed models ·{" "}
            <span className={totalLivePnl >= 0 ? "text-emerald-400" : "text-red-400"}>
              {fmtMoney(totalLivePnl)} live P&L (14d)
            </span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { fetchRows(); fetchRetrainData(); }}
            className="px-3 py-2 text-xs rounded-lg border hover:bg-white/5 flex items-center gap-1.5"
            style={{ borderColor: "var(--border)" }}
          >
            <RefreshCw size={12} /> Refresh
          </button>
          {retraining && (
            <span className="flex items-center gap-2 px-3 py-2 text-xs rounded-lg bg-violet-500/10 text-violet-300 border border-violet-500/30">
              <Loader2 size={12} className="animate-spin" />
              Training {retrainSymbol}: {retrainProgress || "…"}
            </span>
          )}
        </div>
      </div>

      {/* ── Symbol cards ────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {rows.map((row) => (
          <SymbolCard
            key={row.symbol}
            row={row}
            expanded={!!expanded[row.symbol]}
            onToggle={() => setExpanded((e) => ({ ...e, [row.symbol]: !e[row.symbol] }))}
            onRetrain={() => setWizardFor(row)}
            onAnalyse={(pipeline) => setAiFor({ row, pipeline })}
            retraining={retraining && retrainSymbol === row.symbol}
          />
        ))}
      </div>

      {/* ── Schedule card ───────────────────────────────────────────── */}
      <Glass padding="md">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <Clock size={16} className="text-violet-400" />
            <div>
              <h3 className="text-sm font-semibold">Scheduled Retrain</h3>
              <p className="text-xs" style={{ color: "var(--muted)" }}>
                {schedule?.enabled
                  ? `Cron: ${schedule.cron_expression} · Next: ${schedule.next_run?.slice(0, 16) || "—"}`
                  : "Disabled — retrains run only when triggered manually"}
              </p>
            </div>
          </div>
          <button
            onClick={toggleSchedule}
            disabled={!schedule}
            className={`px-3 py-2 text-xs font-medium rounded-lg ${
              schedule?.enabled
                ? "bg-red-500/15 text-red-400 border border-red-500/40 hover:bg-red-500/25"
                : "bg-emerald-500/15 text-emerald-400 border border-emerald-500/40 hover:bg-emerald-500/25"
            }`}
          >
            {schedule?.enabled ? "Disable" : "Enable"}
          </button>
        </div>
      </Glass>

      {/* ── History ─────────────────────────────────────────────────── */}
      {retrainHistory.length > 0 && (
        <Glass padding="md">
          <div className="flex items-center gap-2 mb-3">
            <History size={16} className="text-violet-400" />
            <h3 className="text-sm font-semibold">Retrain History</h3>
            <span className="text-xs" style={{ color: "var(--muted)" }}>({retrainHistory.length})</span>
          </div>
          <div className="space-y-1.5">
            {retrainHistory.map((r) => (
              <div
                key={r.id}
                className="flex items-center gap-3 text-xs px-2 py-1.5 rounded hover:bg-white/5"
              >
                <span className="w-20 flex-shrink-0 tabular-nums" style={{ color: "var(--muted)" }}>
                  {fmtDate(r.started_at)}
                </span>
                <span className="w-20 flex-shrink-0 font-medium">{r.symbol}</span>
                <span className="w-16 flex-shrink-0" style={{ color: "var(--muted)" }}>{r.triggered_by}</span>
                <div className="flex items-center gap-1 flex-shrink-0">
                  <GradeChip grade={r.old_grade || "?"} size="sm" />
                  <span style={{ color: "var(--muted)" }}>→</span>
                  <GradeChip grade={r.new_grade || "?"} size="sm" />
                </div>
                <span className="flex-1" />
                <span className={`text-[10px] px-1.5 py-0.5 rounded-full border ${
                  r.swapped
                    ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10"
                    : "text-slate-400 border-slate-500/40 bg-slate-500/10"
                }`}>
                  {r.swapped ? "swapped" : "held"}
                </span>
                <span className="text-[10px]" style={{ color: "var(--muted)" }}>
                  {r.status}
                </span>
              </div>
            ))}
          </div>
        </Glass>
      )}

      {wizardFor && (
        <RetrainWizard
          row={wizardFor}
          onClose={() => setWizardFor(null)}
          onStart={(opts) => startRetrain(wizardFor.symbol, opts)}
          busy={retraining}
        />
      )}
      {aiFor && (
        <AIInsightDrawer
          row={aiFor.row}
          pipeline={aiFor.pipeline}
          onClose={() => setAiFor(null)}
        />
      )}
    </div>
  );
}

// ── Symbol card ──────────────────────────────────────────────────────────

function SymbolCard({
  row, expanded, onToggle, onRetrain, onAnalyse, retraining,
}: {
  row: SymbolRow;
  expanded: boolean;
  onToggle: () => void;
  onRetrain: () => void;
  onAnalyse: (pipeline: string) => void;
  retraining: boolean;
}) {
  const live = row.live_14d;
  const liveColor = live.total_pnl > 0 ? "text-emerald-400" : live.total_pnl < 0 ? "text-red-400" : "text-slate-400";
  const rr = live.avg_loss !== 0 ? Math.abs(live.avg_win / live.avg_loss) : 0;

  // Group models by pipeline
  const byPipeline = row.models.reduce((acc, m) => {
    (acc[m.pipeline] = acc[m.pipeline] || []).push(m);
    return acc;
  }, {} as Record<string, ModelVariant[]>);
  const pipelines = Object.keys(byPipeline).sort();
  const bestGradePerPipeline: Record<string, string> = {};
  for (const p of pipelines) {
    const order = ["A", "B", "C", "D", "F", "?"];
    bestGradePerPipeline[p] = byPipeline[p]
      .map((m) => m.grade)
      .sort((a, b) => order.indexOf(a) - order.indexOf(b))[0] || "?";
  }

  return (
    <Glass padding="md" className="relative overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex-shrink-0">
            {pipelines.length > 0 ? (
              <GradeChip grade={bestGradePerPipeline[pipelines[0]]} />
            ) : (
              <div className="w-8 h-8 rounded-lg bg-slate-500/10 border border-slate-500/40 flex items-center justify-center text-slate-500 text-xs">
                —
              </div>
            )}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-bold text-base">{row.symbol}</span>
              <span className="text-[10px] uppercase px-1.5 py-0.5 rounded border" style={{ color: "var(--muted)", borderColor: "var(--border)" }}>
                {row.asset_class}
              </span>
            </div>
            <div className="flex items-center gap-1.5 mt-1">
              {row.agents.slice(0, 3).map((a) => (
                <span key={a.id} className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-white/5">
                  <StatusDot status={a.status} />
                  <span className="truncate max-w-[80px]">{a.name}</span>
                </span>
              ))}
              {row.agents.length === 0 && (
                <span className="text-[10px]" style={{ color: "var(--muted)" }}>
                  No agents using this model
                </span>
              )}
              {row.agents.length > 3 && (
                <span className="text-[10px]" style={{ color: "var(--muted)" }}>
                  +{row.agents.length - 3}
                </span>
              )}
            </div>
          </div>
        </div>
        <button
          onClick={onRetrain}
          disabled={retraining}
          className="px-2.5 py-1.5 text-xs font-medium rounded-lg border hover:bg-white/5 disabled:opacity-30 flex items-center gap-1.5 flex-shrink-0"
          style={{ borderColor: "var(--border)" }}
        >
          {retraining ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          Retrain
        </button>
      </div>

      {/* 14-day live stats strip */}
      <div className="grid grid-cols-4 gap-2 mb-3">
        <Stat label="14d P&L" value={fmtMoney(live.total_pnl)} color={liveColor} />
        <Stat label="Trades" value={live.trades.toString()} />
        <Stat label="Win rate" value={live.trades ? `${live.win_rate}%` : "—"} />
        <Stat label="R:R" value={rr ? rr.toFixed(2) : "—"} />
      </div>

      {/* Last retrain summary — surfaced inline so users don't have to
          expand the details view to see the most recent training result. */}
      {row.last_retrain && (
        <div className="mb-3 p-2 rounded-lg border text-[11px] flex items-center justify-between gap-2"
             style={{ borderColor: "var(--border)", background: "rgba(139,92,246,0.04)" }}>
          <div className="flex items-center gap-2 flex-wrap">
            <span style={{ color: "var(--muted)" }}>Last retrain</span>
            <span>{fmtDate(row.last_retrain.started_at)}</span>
            <span style={{ color: "var(--muted)" }}>·</span>
            <GradeChip grade={row.last_retrain.old_grade || "?"} size="sm" />
            <span style={{ color: "var(--muted)" }}>→</span>
            <GradeChip grade={row.last_retrain.new_grade || "?"} size="sm" />
            {row.last_retrain.old_sharpe != null && row.last_retrain.new_sharpe != null && (
              <span className="text-[10px]" style={{ color: "var(--muted)" }}>
                Sharpe {row.last_retrain.old_sharpe.toFixed(2)} → {row.last_retrain.new_sharpe.toFixed(2)}
              </span>
            )}
          </div>
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded"
            style={
              row.last_retrain.swapped
                ? { background: "rgba(16,185,129,0.15)", color: "#34d399" }
                : { background: "rgba(245,158,11,0.15)", color: "#f59e0b" }
            }
          >
            {row.last_retrain.swapped ? "swapped" : "held (old kept)"}
          </span>
        </div>
      )}

      {/* Models by pipeline */}
      {pipelines.length === 0 ? (
        <p className="text-xs text-center py-4" style={{ color: "var(--muted)" }}>
          No deployed models for this symbol. Click Retrain to build one.
        </p>
      ) : (
        <div className="space-y-2">
          {pipelines.map((p) => (
            <PipelineBlock
              key={p}
              pipeline={p}
              variants={byPipeline[p]}
              onAnalyse={() => onAnalyse(p)}
            />
          ))}
        </div>
      )}

      {/* Expand toggle */}
      <button
        onClick={onToggle}
        className="mt-3 w-full text-xs py-1 rounded hover:bg-white/5 flex items-center justify-center gap-1"
        style={{ color: "var(--muted)" }}
      >
        {expanded ? (
          <>Hide details <ChevronUp size={12} /></>
        ) : (
          <>Show details <ChevronDown size={12} /></>
        )}
      </button>

      {expanded && (
        <div className="mt-3 pt-3 border-t space-y-2 text-xs" style={{ borderColor: "var(--border)" }}>
          <div className="grid grid-cols-2 gap-2">
            <InfoRow label="avg win" value={fmtMoney(live.avg_win)} color="text-emerald-400" />
            <InfoRow label="avg loss" value={fmtMoney(live.avg_loss)} color="text-red-400" />
          </div>
          {row.last_retrain && (
            <div className="pt-2 border-t" style={{ borderColor: "var(--border)" }}>
              <div style={{ color: "var(--muted)" }}>Last retrain</div>
              <div className="flex items-center gap-2 mt-1">
                <span>{fmtDate(row.last_retrain.started_at)}</span>
                <span style={{ color: "var(--muted)" }}>·</span>
                <GradeChip grade={row.last_retrain.old_grade || "?"} size="sm" />
                <span style={{ color: "var(--muted)" }}>→</span>
                <GradeChip grade={row.last_retrain.new_grade || "?"} size="sm" />
                <span className="text-[10px]" style={{ color: "var(--muted)" }}>
                  {row.last_retrain.swapped ? "swapped" : "held"}
                </span>
              </div>
              {row.last_retrain.error && (
                <p className="text-[10px] text-red-400 mt-1">{row.last_retrain.error}</p>
              )}
            </div>
          )}
          {row.agents.length > 0 && (
            <div className="pt-2 border-t" style={{ borderColor: "var(--border)" }}>
              <div style={{ color: "var(--muted)" }}>All agents</div>
              <div className="flex flex-wrap gap-1 mt-1">
                {row.agents.map((a) => (
                  <span key={a.id} className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-white/5">
                    <StatusDot status={a.status} />
                    <span className="font-medium">{a.name}</span>
                    <span className="text-[10px]" style={{ color: "var(--muted)" }}>
                      · {a.agent_type} · {a.broker}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Glass>
  );
}

function PipelineBlock({
  pipeline, variants, onAnalyse,
}: {
  pipeline: string;
  variants: ModelVariant[];
  onAnalyse: () => void;
}) {
  // Scout is a proxy pipeline — same joblib files as Potential, different
  // runtime. Surface this so users don't think it's a separate training.
  const isProxy = Boolean((variants[0] as ModelVariant & { proxy_for?: string })?.proxy_for);
  const proxySource = (variants[0] as ModelVariant & { proxy_for?: string })?.proxy_for;
  return (
    <div className="p-2 rounded-lg border" style={{ borderColor: "var(--border)", background: "rgba(255,255,255,0.02)" }}>
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <Zap size={12} className={isProxy ? "text-amber-400" : "text-violet-400"} />
          <span className="text-xs font-semibold uppercase tracking-wide">{pipeline}</span>
          <span className="text-[10px]" style={{ color: "var(--muted)" }}>
            {variants[0]?.feature_count || 0} features
          </span>
          {isProxy && (
            <span
              className="text-[9px] px-1 py-0.5 rounded"
              style={{ background: "rgba(245,158,11,0.12)", color: "#f59e0b" }}
              title={`Scout reuses the ${proxySource} joblib — no separate training.`}
            >
              reuses {proxySource}
            </span>
          )}
        </div>
        <button
          onClick={onAnalyse}
          className="text-[10px] flex items-center gap-1 px-2 py-0.5 rounded hover:bg-white/10"
          style={{ color: "var(--muted)" }}
        >
          <Sparkles size={10} /> Analyse with AI
        </button>
      </div>
      <div className="space-y-0.5">
        {variants.map((v) => (
          <div key={v.file} className="flex items-center gap-2 text-xs py-0.5">
            <GradeChip grade={v.grade} size="sm" />
            <span className="w-16 flex-shrink-0">{v.model_type}</span>
            <span className="text-[10px] tabular-nums" style={{ color: "var(--muted)" }}>
              Sharpe {v.sharpe.toFixed(2)}
            </span>
            <span className="text-[10px] tabular-nums" style={{ color: "var(--muted)" }}>
              WR {v.win_rate.toFixed(1)}%
            </span>
            <span className="text-[10px] tabular-nums" style={{ color: "var(--muted)" }}>
              PF {v.profit_factor.toFixed(2)}
            </span>
            <span className="flex-1" />
            <span className="text-[10px]" style={{ color: "var(--muted)" }}>
              {fmtDate(v.trained_at)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="px-2 py-1.5 rounded-lg" style={{ background: "rgba(255,255,255,0.02)" }}>
      <div className="text-[10px]" style={{ color: "var(--muted)" }}>{label}</div>
      <div className={`text-sm font-semibold tabular-nums ${color || ""}`}>{value}</div>
    </div>
  );
}

function InfoRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <span style={{ color: "var(--muted)" }}>{label}: </span>
      <span className={`font-medium tabular-nums ${color || ""}`}>{value}</span>
    </div>
  );
}

// ── Retrain wizard modal ─────────────────────────────────────────────────

function RetrainWizard({
  row, onClose, onStart, busy,
}: {
  row: SymbolRow;
  onClose: () => void;
  onStart: (opts: {
    pipeline: "flowrex_v2" | "potential";
    train_months: number;
    n_trials: number;
    refresh_dukascopy: boolean;
  }) => void;
  busy: boolean;
}) {
  const [pipeline, setPipeline] = useState<"flowrex_v2" | "potential">(
    (row.models[0]?.pipeline === "potential" ? "potential" : "flowrex_v2") as "flowrex_v2" | "potential"
  );
  const [months, setMonths] = useState(6);
  const [trials, setTrials] = useState(15);
  const [refresh, setRefresh] = useState(false);

  const etaMin = pipeline === "potential" ? Math.ceil(40 * (trials / 15)) : Math.ceil(15 * (trials / 15));

  return (
    <Modal open onClose={onClose} title={`Retrain ${row.symbol}`}>
      <div className="space-y-4 text-sm">
        {/* Pipeline chooser */}
        <div>
          <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted)" }}>
            Pipeline
          </label>
          <div className="grid grid-cols-2 gap-2">
            <PipelineOption
              selected={pipeline === "flowrex_v2"}
              onClick={() => setPipeline("flowrex_v2")}
              title="flowrex_v2"
              subtitle="120 features, 3-model ensemble, grade-gated swap"
              tag="Safe — won't downgrade"
              tagColor="text-emerald-400 border-emerald-500/40"
            />
            <PipelineOption
              selected={pipeline === "potential"}
              onClick={() => setPipeline("potential")}
              title="potential"
              subtitle="85 features, walk-forward, writes unconditionally. Scout agents reuse this same model — one retrain covers both."
              tag="Warning — check new grade"
              tagColor="text-amber-400 border-amber-500/40"
            />
          </div>
        </div>

        {/* Window + trials */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted)" }}>
              Training window
            </label>
            <select
              value={months}
              onChange={(e) => setMonths(parseInt(e.target.value))}
              className="w-full px-3 py-2 rounded-lg border bg-transparent"
              style={{ borderColor: "var(--border)", background: "var(--card)" }}
            >
              <option value={3}>3 months (fastest, most recent regime)</option>
              <option value={6}>6 months (default)</option>
              <option value={9}>9 months</option>
              <option value={12}>12 months (longer history, risks regime break)</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted)" }}>
              Hyperparameter trials
            </label>
            <select
              value={trials}
              onChange={(e) => setTrials(parseInt(e.target.value))}
              className="w-full px-3 py-2 rounded-lg border bg-transparent"
              style={{ borderColor: "var(--border)", background: "var(--card)" }}
            >
              <option value={10}>10 — fast</option>
              <option value={15}>15 — default</option>
              <option value={25}>25 — thorough</option>
              <option value={40}>40 — exhaustive</option>
            </select>
          </div>
        </div>

        {/* Refresh toggle */}
        <label className="flex items-center gap-2 cursor-pointer p-2 rounded-lg border"
          style={{ borderColor: "var(--border)" }}>
          <input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} />
          <div className="flex-1">
            <div className="text-sm font-medium">Refresh Dukascopy first</div>
            <div className="text-xs" style={{ color: "var(--muted)" }}>
              Delta-merge fresh bars into the persistent CSV before training. Adds ~5-25 s.
            </div>
          </div>
        </label>

        {/* ETA + what-happens summary */}
        <div className="p-3 rounded-lg border text-xs" style={{ borderColor: "var(--border)", background: "rgba(139,92,246,0.05)" }}>
          <div className="flex items-center gap-1.5 mb-1">
            <Clock size={12} className="text-violet-400" />
            <span className="font-medium">Estimated time: ~{etaMin} min</span>
          </div>
          <p style={{ color: "var(--muted)" }}>
            Runs in the background. You can leave this page — training progress appears
            in the header bar. Once complete, check this symbol&apos;s card to verify the new grade.
            {pipeline === "potential" && " The potential pipeline writes directly — if the new grade is worse, check retrain history and re-run on a different window."}
          </p>
        </div>

        {/* Actions */}
        <div className="flex gap-2 justify-end">
          <button
            onClick={onClose}
            className="px-3 py-2 text-xs rounded-lg border hover:bg-white/5"
            style={{ borderColor: "var(--border)" }}
          >
            Cancel
          </button>
          <button
            onClick={() => onStart({ pipeline, train_months: months, n_trials: trials, refresh_dukascopy: refresh })}
            disabled={busy}
            className="px-3 py-2 text-xs rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-medium disabled:opacity-50 flex items-center gap-1.5"
          >
            <Play size={12} /> Start retrain
          </button>
        </div>
      </div>
    </Modal>
  );
}

function PipelineOption({
  selected, onClick, title, subtitle, tag, tagColor,
}: {
  selected: boolean;
  onClick: () => void;
  title: string;
  subtitle: string;
  tag: string;
  tagColor: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`text-left p-2.5 rounded-lg border transition-colors ${
        selected ? "border-violet-500 bg-violet-500/10" : "hover:bg-white/5"
      }`}
      style={{ borderColor: selected ? undefined : "var(--border)" }}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-semibold">{title}</span>
        {selected && <span className="text-[10px] text-violet-400">✓</span>}
      </div>
      <p className="text-[10px] mb-1.5" style={{ color: "var(--muted)" }}>{subtitle}</p>
      <span className={`inline-block text-[9px] px-1.5 py-0.5 rounded border ${tagColor}`}>
        {tag}
      </span>
    </button>
  );
}

// ── AI insight drawer ──────────────────────────────────────────────────

function AIInsightDrawer({
  row, pipeline, onClose,
}: {
  row: SymbolRow;
  pipeline: string;
  onClose: () => void;
}) {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.post("/api/ml/analyze", { symbol: row.symbol, pipeline })
      .then((r) => { if (!cancelled) setMarkdown(r.data?.markdown || "_No response._"); })
      .catch((e) => { if (!cancelled) setError(getErrorMessage(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [row.symbol, pipeline]);

  return (
    <div className="fixed inset-0 z-50 flex" onClick={onClose}>
      <div className="flex-1 bg-black/50" />
      <div
        className="w-full max-w-lg h-full bg-[var(--background)] border-l overflow-y-auto flex flex-col"
        style={{ borderColor: "var(--border)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b sticky top-0 z-10"
          style={{ borderColor: "var(--border)", background: "var(--background)" }}>
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-violet-400" />
            <h3 className="text-sm font-semibold">
              AI analysis · {row.symbol} · {pipeline}
            </h3>
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-white/10">
            <X size={14} />
          </button>
        </div>
        <div className="flex-1 p-4">
          {loading && (
            <div className="flex items-center gap-2 text-sm" style={{ color: "var(--muted)" }}>
              <Loader2 size={14} className="animate-spin" />
              Asking the AI supervisor…
            </div>
          )}
          {error && (
            <div className="text-sm text-red-400 whitespace-pre-wrap">{error}</div>
          )}
          {!loading && !error && markdown && (
            <div className="text-sm whitespace-pre-wrap leading-relaxed" style={{ color: "var(--foreground)" }}>
              {markdown}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
