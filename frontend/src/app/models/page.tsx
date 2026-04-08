"use client";

import { useEffect, useState, useMemo } from "react";
import api from "@/lib/api";
import type { MLModel } from "@/types";
import Card from "@/components/ui/Card";
import StatusBadge from "@/components/ui/StatusBadge";
import ModelDetailModal from "@/components/ModelDetailModal";
import { BrainCircuit, Loader2, RefreshCw, Calendar, History, ArrowRight, ChevronDown, ChevronUp, Zap, BarChart3, Shield, Database } from "lucide-react";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";

interface PotentialModel {
  symbol: string;
  asset_class: string;
  model_type: string;
  grade: string;
  sharpe: number;
  win_rate: number;
  max_drawdown: number;
  total_return: number;
  profit_factor: number;
  total_trades: number;
  accuracy: number;
  pipeline_version: string;
  trained_at: string;
  feature_count: number;
  data_source: string;
  top_features: { feature: string; importance: number }[];
  oos_start: string;
  file_path: string;
}

interface RetrainRun {
  id: number; symbol: string; triggered_by: string; started_at: string;
  finished_at: string | null; status: string; old_grade: string | null;
  new_grade: string | null; old_sharpe: number | null; new_sharpe: number | null;
  swapped: boolean; swap_reason: string | null;
}
interface RetrainSchedule { enabled: boolean; cron_expression: string; next_run: string | null; }

const GRADE_COLORS: Record<string, { bg: string; text: string; glow: string; border: string }> = {
  A: { bg: "bg-emerald-500/10", text: "text-emerald-400", glow: "shadow-[0_0_12px_rgba(34,197,94,0.35)]", border: "border-emerald-500/40" },
  B: { bg: "bg-blue-500/10", text: "text-blue-400", glow: "shadow-[0_0_12px_rgba(59,130,246,0.35)]", border: "border-blue-500/40" },
  C: { bg: "bg-amber-500/10", text: "text-amber-400", glow: "shadow-[0_0_12px_rgba(245,158,11,0.35)]", border: "border-amber-500/40" },
  D: { bg: "bg-orange-500/10", text: "text-orange-400", glow: "shadow-[0_0_12px_rgba(249,115,22,0.35)]", border: "border-orange-500/40" },
  F: { bg: "bg-red-500/10", text: "text-red-400", glow: "shadow-[0_0_12px_rgba(239,68,68,0.35)]", border: "border-red-500/40" },
};

function GradeBadge({ grade }: { grade: string }) {
  const colors = GRADE_COLORS[grade] || GRADE_COLORS.F;
  return (
    <span className={`inline-flex items-center justify-center w-10 h-10 rounded-lg text-lg font-bold ${colors.bg} ${colors.text} ${colors.glow} border ${colors.border}`}>
      {grade}
    </span>
  );
}

export default function ModelsPage() {
  const [models, setModels] = useState<MLModel[]>([]);
  const [potentialModels, setPotentialModels] = useState<PotentialModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [training, setTraining] = useState(false);
  const [trainingStatus, setTrainingStatus] = useState("");
  const [selectedModel, setSelectedModel] = useState<MLModel | null>(null);
  const [symbolFilter, setSymbolFilter] = useState("all");
  const [expandedShap, setExpandedShap] = useState<Record<string, boolean>>({});

  // Retrain state
  const [retraining, setRetraining] = useState(false);
  const [retrainSymbol, setRetrainSymbol] = useState<string | null>(null);
  const [retrainProgress, setRetrainProgress] = useState("");
  const [retrainHistory, setRetrainHistory] = useState<RetrainRun[]>([]);
  const [schedule, setSchedule] = useState<RetrainSchedule | null>(null);

  const fetchModels = () => {
    api.get("/api/ml/models").then((r) => setModels(r.data)).catch((e) => console.warn("fetch failed:", e?.message)).finally(() => setLoading(false));
  };
  const fetchPotentialModels = () => {
    api.get("/api/ml/potential-models").then((r) => setPotentialModels(r.data)).catch((e) => console.warn("fetch failed:", e?.message));
  };
  const fetchRetrainData = () => {
    api.get("/api/ml/retrain/history?limit=10").then((r) => setRetrainHistory(r.data)).catch((e) => console.warn("fetch failed:", e?.message));
    api.get("/api/ml/retrain/schedule").then((r) => setSchedule(r.data)).catch((e) => console.warn("fetch failed:", e?.message));
  };
  useEffect(() => { fetchModels(); fetchPotentialModels(); fetchRetrainData(); }, []);

  useEffect(() => {
    if (!training) return;
    const poll = setInterval(async () => {
      try {
        const res = await api.get("/api/ml/training-status");
        setTrainingStatus(res.data.progress || "");
        if (!res.data.active) { setTraining(false); fetchModels(); toast.success("Training complete"); }
      } catch { /* poll */ }
    }, 3000);
    return () => clearInterval(poll);
  }, [training]);

  // Poll retrain status
  useEffect(() => {
    if (!retraining) return;
    const poll = setInterval(async () => {
      try {
        const res = await api.get("/api/ml/retrain/status");
        setRetrainProgress(res.data.progress || "");
        if (!res.data.active) {
          setRetraining(false);
          setRetrainSymbol(null);
          fetchModels();
          fetchPotentialModels();
          fetchRetrainData();
          toast.success("Monthly retrain complete");
        }
      } catch { /* poll */ }
    }, 3000);
    return () => clearInterval(poll);
  }, [retraining]);

  const handleRetrain = async (symbol?: string) => {
    try {
      const url = symbol ? "/api/ml/retrain" : "/api/ml/retrain/all";
      const body = symbol ? { symbol } : undefined;
      const res = await api.post(url, body);
      if (res.data.status === "started") {
        setRetraining(true);
        setRetrainSymbol(symbol || "ALL");
        setRetrainProgress("Starting...");
        toast.success(symbol ? `Retraining ${symbol}` : "Retraining all symbols");
      } else {
        toast.error(res.data.message || "Busy");
      }
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const toggleSchedule = async () => {
    if (!schedule) return;
    try {
      const res = await api.post("/api/ml/retrain/schedule", {
        enabled: !schedule.enabled,
        cron_expression: schedule.cron_expression,
      });
      setSchedule(res.data);
      toast.success(res.data.enabled ? "Schedule enabled" : "Schedule disabled");
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleTrain = async (symbol: string) => {
    try {
      const res = await api.post("/api/ml/train", { symbol, pipeline: "scalping", timeframe: "M5" });
      if (res.data.status === "started") { setTraining(true); setTrainingStatus("Starting..."); toast.success("Training " + symbol); }
      else toast.error(res.data.message || "Busy");
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const fmt = (v: number | undefined) => v !== undefined ? v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "\u2014";
  const symbols = useMemo(() => [...new Set(models.map((m) => m.symbol))].sort(), [models]);
  const filteredSymbols = symbolFilter === "all" ? symbols : symbols.filter((s) => s === symbolFilter);
  const gradeMap: Record<string, number> = { A: 4, B: 3, C: 2, D: 1, F: 0 };
  const gradeReverse: Record<number, string> = { 4: "A", 3: "B", 2: "C", 1: "D", 0: "F" };

  const toggleShap = (symbol: string) => {
    setExpandedShap((prev) => ({ ...prev, [symbol]: !prev[symbol] }));
  };

  if (loading) return <div className="flex items-center justify-center h-64"><Loader2 size={24} className="animate-spin" style={{ color: "var(--muted)" }} /></div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold bg-gradient-to-r from-violet-400 to-blue-400 bg-clip-text text-transparent">ML Models</h1>
          <p className="text-xs mt-0.5" style={{ color: "var(--muted)" }}>{potentialModels.length} deployed models | Potential Agent v2 Pipeline</p>
        </div>
        <div className="flex gap-2">
          {(training || retraining) && (
            <span className="flex items-center gap-2 px-3 py-2 text-xs rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/30">
              <Loader2 size={14} className="animate-spin" /> {retraining ? retrainProgress : trainingStatus}
            </span>
          )}
          <button onClick={() => { fetchModels(); fetchPotentialModels(); }} className="p-2 rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }} title="Refresh"><RefreshCw size={16} style={{ color: "var(--muted)" }} /></button>
        </div>
      </div>

      {/* ── Potential Agent v2 — Deployed Models ─────────────────────────── */}
      {potentialModels.length > 0 && (
        <>
          <div className="flex items-center gap-2">
            <Zap size={16} className="text-violet-400" />
            <h2 className="text-sm font-semibold bg-gradient-to-r from-violet-400 to-blue-400 bg-clip-text text-transparent">Deployed Models</h2>
            <span className="text-xs px-2 py-0.5 rounded-full bg-violet-500/15 text-violet-400 border border-violet-500/30">potential_v2</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {potentialModels.map((pm) => {
              const gc = GRADE_COLORS[pm.grade] || GRADE_COLORS.F;
              const isRetrainingThis = retraining && (retrainSymbol === pm.symbol || retrainSymbol === "ALL");
              return (
                <Card key={pm.symbol} className={`relative overflow-hidden ${gc.glow}`}>
                  {/* Gradient accent top bar */}
                  <div className={`absolute top-0 left-0 right-0 h-0.5 bg-gradient-to-r ${
                    pm.grade === "A" ? "from-emerald-500 to-emerald-300" :
                    pm.grade === "B" ? "from-blue-500 to-blue-300" :
                    pm.grade === "C" ? "from-amber-500 to-amber-300" :
                    "from-red-500 to-red-300"
                  }`} />

                  {/* Header: Symbol + Grade */}
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <GradeBadge grade={pm.grade} />
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-base font-bold">{pm.symbol}</span>
                          <span className="text-xs px-1.5 py-0.5 rounded bg-white/5" style={{ color: "var(--muted)" }}>{pm.asset_class}</span>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <StatusBadge value={pm.model_type} />
                          <span className="text-xs" style={{ color: "var(--muted)" }}>{pm.feature_count} features</span>
                        </div>
                      </div>
                    </div>
                    <button
                      onClick={() => handleRetrain(pm.symbol)}
                      disabled={retraining || training}
                      className="px-2.5 py-1.5 text-xs font-medium rounded-lg border hover:bg-white/5 disabled:opacity-30 transition-colors flex items-center gap-1.5"
                      style={{ borderColor: "var(--border)" }}
                    >
                      {isRetrainingThis ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                      {isRetrainingThis ? "Training..." : "Retrain"}
                    </button>
                  </div>

                  {/* Key Metrics Grid */}
                  <div className="grid grid-cols-3 gap-2 mb-3">
                    <div className="text-center p-2 rounded-lg" style={{ background: "var(--sidebar-active)" }}>
                      <div className="text-xs" style={{ color: "var(--muted)" }}>Sharpe</div>
                      <div className="text-sm font-bold text-white">{pm.sharpe.toFixed(2)}</div>
                    </div>
                    <div className="text-center p-2 rounded-lg" style={{ background: "var(--sidebar-active)" }}>
                      <div className="text-xs" style={{ color: "var(--muted)" }}>Win Rate</div>
                      <div className="text-sm font-bold text-white">{pm.win_rate.toFixed(1)}%</div>
                    </div>
                    <div className="text-center p-2 rounded-lg" style={{ background: "var(--sidebar-active)" }}>
                      <div className="text-xs" style={{ color: "var(--muted)" }}>Max DD</div>
                      <div className="text-sm font-bold text-red-400">{pm.max_drawdown.toFixed(2)}%</div>
                    </div>
                  </div>

                  {/* Secondary Metrics */}
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs mb-3">
                    <div className="flex justify-between">
                      <span style={{ color: "var(--muted)" }}>Profit Factor</span>
                      <span className="text-white font-medium">{pm.profit_factor.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span style={{ color: "var(--muted)" }}>Total Return</span>
                      <span className="text-emerald-400 font-medium">{pm.total_return.toFixed(1)}%</span>
                    </div>
                    <div className="flex justify-between">
                      <span style={{ color: "var(--muted)" }}>OOS Trades</span>
                      <span className="text-white font-medium">{pm.total_trades}</span>
                    </div>
                    <div className="flex justify-between">
                      <span style={{ color: "var(--muted)" }}>Accuracy</span>
                      <span className="text-white font-medium">{(pm.accuracy * 100).toFixed(1)}%</span>
                    </div>
                  </div>

                  {/* Meta Info */}
                  <div className="flex items-center gap-3 text-xs mb-2" style={{ color: "var(--muted)" }}>
                    <span className="flex items-center gap-1"><Database size={10} /> {pm.data_source}</span>
                  </div>
                  <div className="flex items-center gap-3 text-xs" style={{ color: "var(--muted)" }}>
                    <span className="flex items-center gap-1"><Calendar size={10} /> Trained {pm.trained_at ? new Date(pm.trained_at).toLocaleDateString() : "Unknown"}</span>
                    {pm.oos_start && <span>OOS from {pm.oos_start}</span>}
                  </div>

                  {/* SHAP Feature Importance (expandable) */}
                  {pm.top_features.length > 0 && (
                    <div className="mt-3 border-t pt-2" style={{ borderColor: "var(--border)" }}>
                      <button
                        onClick={() => toggleShap(pm.symbol)}
                        className="flex items-center gap-1.5 text-xs font-medium w-full hover:text-white transition-colors"
                        style={{ color: "var(--muted)" }}
                      >
                        <BarChart3 size={12} />
                        Top Features
                        {expandedShap[pm.symbol] ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                      </button>
                      {expandedShap[pm.symbol] && (
                        <div className="mt-2 space-y-1.5">
                          {pm.top_features.map((f, idx) => (
                            <div key={f.feature} className="flex items-center gap-2 text-xs">
                              <span className="w-4 text-right font-medium" style={{ color: "var(--muted)" }}>{idx + 1}</span>
                              <span className="w-44 truncate" title={f.feature} style={{ color: "var(--muted)" }}>{f.feature.replace("pot_", "")}</span>
                              <div className="flex-1 h-2.5 rounded-full overflow-hidden" style={{ background: "var(--sidebar-active)" }}>
                                <div
                                  className={`h-full rounded-full ${
                                    pm.grade === "A" ? "bg-gradient-to-r from-emerald-600 to-emerald-400" :
                                    pm.grade === "B" ? "bg-gradient-to-r from-blue-600 to-blue-400" :
                                    "bg-gradient-to-r from-violet-600 to-violet-400"
                                  }`}
                                  style={{ width: `${f.importance * 100}%` }}
                                />
                              </div>
                              <span className="w-10 text-right tabular-nums" style={{ color: "var(--muted)" }}>{(f.importance * 100).toFixed(0)}%</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        </>
      )}

      {/* ── Legacy DB Models ──────────────────────────────────────────────── */}
      {symbols.length > 0 && models.length > 0 && (
        <>
          <div className="flex items-center gap-2 mt-2">
            <Shield size={16} style={{ color: "var(--muted)" }} />
            <h2 className="text-sm font-semibold" style={{ color: "var(--muted)" }}>Database Models (Legacy Pipelines)</h2>
          </div>

          <div className="flex gap-1 overflow-x-auto">
            <button onClick={() => setSymbolFilter("all")} className={"px-3 py-1.5 text-xs font-medium rounded-lg transition-colors " + (symbolFilter === "all" ? "bg-blue-600 text-white" : "hover:bg-white/10")} style={{ color: symbolFilter === "all" ? undefined : "var(--muted)" }}>All</button>
            {symbols.map((s) => <button key={s} onClick={() => setSymbolFilter(s)} className={"px-3 py-1.5 text-xs font-medium rounded-lg transition-colors " + (symbolFilter === s ? "bg-blue-600 text-white" : "hover:bg-white/10")} style={{ color: symbolFilter === s ? undefined : "var(--muted)" }}>{s}</button>)}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredSymbols.map((sym) => {
              const symModels = models.filter((m) => m.symbol === sym);
              const graded = symModels.filter((m) => m.grade);
              const avgNum = graded.length > 0 ? graded.reduce((s, m) => s + (gradeMap[m.grade!] ?? 0), 0) / graded.length : -1;
              const avgGrade = avgNum >= 0 ? gradeReverse[Math.round(avgNum)] : null;
              return (
                <Card key={sym}>
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <BrainCircuit size={18} style={{ color: "var(--accent)" }} />
                      <span className="font-semibold text-sm">{sym}</span>
                      {avgGrade && <StatusBadge value={avgGrade} />}
                      <span className="text-xs" style={{ color: "var(--muted)" }}>{symModels.length} models</span>
                    </div>
                    <button onClick={() => handleTrain(sym)} disabled={training} className="px-2.5 py-1 text-xs rounded border hover:bg-white/5 disabled:opacity-30" style={{ borderColor: "var(--border)" }}>{training ? "..." : "Retrain"}</button>
                  </div>
                  <div className="space-y-2">
                    {symModels.map((m) => (
                      <div key={m.id} onClick={() => setSelectedModel(m)} className="flex items-center justify-between px-3 py-2 rounded-lg hover:bg-white/[0.03] cursor-pointer transition-colors border" style={{ borderColor: "var(--border)" }}>
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium w-20">{m.model_type}</span>
                          <StatusBadge value={m.pipeline} />
                          {m.grade && <span className={m.grade === "A" ? "grade-glow-a rounded" : m.grade === "B" ? "grade-glow-b rounded" : m.grade === "C" ? "grade-glow-c rounded" : m.grade === "F" ? "grade-glow-f rounded" : ""}><StatusBadge value={m.grade} /></span>}
                        </div>
                        <div className="flex items-center gap-3 text-xs" style={{ color: "var(--muted)" }}>
                          {m.metrics?.accuracy && <span>Acc: {(m.metrics.accuracy * 100).toFixed(1)}%</span>}
                          {m.metrics?.sharpe !== undefined && <span>Sharpe: {fmt(m.metrics.sharpe)}</span>}
                          {m.metrics?.win_rate !== undefined && <span>WR: {m.metrics.win_rate.toFixed(0)}%</span>}
                          <span>{m.trained_at ? new Date(m.trained_at).toLocaleDateString() : ""}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              );
            })}
          </div>
        </>
      )}

      {/* Monthly Retrain Controls */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <RefreshCw size={16} style={{ color: "var(--accent)" }} />
            <h3 className="text-sm font-semibold">Monthly Retrain</h3>
            {retraining && (
              <span className="flex items-center gap-1.5 text-xs text-blue-400">
                <Loader2 size={12} className="animate-spin" /> {retrainProgress}
              </span>
            )}
          </div>
          {schedule && (
            <div className="flex items-center gap-2">
              <Calendar size={14} style={{ color: "var(--muted)" }} />
              <span className="text-xs" style={{ color: "var(--muted)" }}>
                {schedule.enabled ? `Next: ${schedule.next_run ? new Date(schedule.next_run).toLocaleDateString() : "1st of month"}` : "Schedule off"}
              </span>
              <button
                onClick={toggleSchedule}
                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${schedule.enabled ? "bg-blue-600" : "bg-white/10"}`}
              >
                <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${schedule.enabled ? "translate-x-5" : "translate-x-0.5"}`} />
              </button>
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {["US30", "BTCUSD", "XAUUSD", "ES", "NAS100"].map((s) => (
            <button
              key={s}
              onClick={() => handleRetrain(s)}
              disabled={retraining || training}
              className="px-3 py-1.5 text-xs font-medium rounded-lg border hover:bg-white/5 disabled:opacity-30 transition-colors"
              style={{ borderColor: "var(--border)" }}
            >
              {retrainSymbol === s ? <span className="flex items-center gap-1"><Loader2 size={10} className="animate-spin" /> {s}</span> : `Retrain ${s}`}
            </button>
          ))}
          <button
            onClick={() => handleRetrain()}
            disabled={retraining || training}
            className="px-4 py-1.5 text-xs font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-30 transition-colors"
          >
            Retrain All
          </button>
        </div>
        <p className="text-xs mt-2" style={{ color: "var(--muted)" }}>
          Trains on last 12 months, validates on 2-week holdout. Only swaps if new model is better.
        </p>
      </Card>

      {/* Retrain History */}
      {retrainHistory.length > 0 && (
        <Card>
          <div className="flex items-center gap-2 mb-3">
            <History size={16} style={{ color: "var(--accent)" }} />
            <h3 className="text-sm font-semibold">Retrain History</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ color: "var(--muted)" }}>
                  <th className="text-left py-1.5 px-2 font-medium">Symbol</th>
                  <th className="text-left py-1.5 px-2 font-medium">Date</th>
                  <th className="text-left py-1.5 px-2 font-medium">Grade</th>
                  <th className="text-left py-1.5 px-2 font-medium">Sharpe</th>
                  <th className="text-left py-1.5 px-2 font-medium">Swapped</th>
                  <th className="text-left py-1.5 px-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {retrainHistory.map((r) => (
                  <tr key={r.id} className="border-t" style={{ borderColor: "var(--border)" }}>
                    <td className="py-1.5 px-2 font-medium">{r.symbol}</td>
                    <td className="py-1.5 px-2" style={{ color: "var(--muted)" }}>{new Date(r.started_at).toLocaleDateString()}</td>
                    <td className="py-1.5 px-2">
                      <span className="flex items-center gap-1">
                        <StatusBadge value={r.old_grade || "—"} />
                        <ArrowRight size={10} style={{ color: "var(--muted)" }} />
                        <StatusBadge value={r.new_grade || "—"} />
                      </span>
                    </td>
                    <td className="py-1.5 px-2" style={{ color: "var(--muted)" }}>
                      {r.old_sharpe?.toFixed(2) ?? "—"} <ArrowRight size={10} className="inline" /> {r.new_sharpe?.toFixed(2) ?? "—"}
                    </td>
                    <td className="py-1.5 px-2">
                      {r.swapped ? (
                        <span className="text-emerald-400">Yes</span>
                      ) : (
                        <span style={{ color: "var(--muted)" }}>No</span>
                      )}
                    </td>
                    <td className="py-1.5 px-2">
                      <StatusBadge value={r.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="text-xs p-3 rounded-lg" style={{ background: "var(--sidebar-active)", color: "var(--muted)" }}>
        <span className="font-medium">Grade Criteria:</span> A=Sharpe&gt;1.5+WR&gt;55%+DD&lt;15% | B=Sharpe&gt;1.0+WR&gt;50%+DD&lt;20% | C=Sharpe&gt;0.5+WR&gt;45%+DD&lt;25% | D=Positive | F=Negative
      </div>

      <ModelDetailModal model={selectedModel} open={selectedModel !== null} onClose={() => setSelectedModel(null)} />
    </div>
  );
}
