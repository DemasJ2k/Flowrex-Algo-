"""Write the updated Models page."""
import os

TARGET = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "app", "models", "page.tsx")

CONTENT = r""""use client";

import { useEffect, useState, useMemo } from "react";
import api from "@/lib/api";
import type { MLModel } from "@/types";
import Card from "@/components/ui/Card";
import StatusBadge from "@/components/ui/StatusBadge";
import ModelDetailModal from "@/components/ModelDetailModal";
import { BrainCircuit, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";

export default function ModelsPage() {
  const [models, setModels] = useState<MLModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [training, setTraining] = useState(false);
  const [trainingStatus, setTrainingStatus] = useState("");
  const [selectedModel, setSelectedModel] = useState<MLModel | null>(null);
  const [symbolFilter, setSymbolFilter] = useState("all");

  const fetchModels = () => {
    api.get("/api/ml/models").then((r) => setModels(r.data)).catch(() => {}).finally(() => setLoading(false));
  };
  useEffect(() => { fetchModels(); }, []);

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

  if (loading) return <div className="flex items-center justify-center h-64"><Loader2 size={24} className="animate-spin" style={{ color: "var(--muted)" }} /></div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">ML Models</h1>
          <p className="text-xs mt-0.5" style={{ color: "var(--muted)" }}>{models.length} models across {symbols.length} symbols</p>
        </div>
        <div className="flex gap-2">
          {training && <span className="flex items-center gap-2 px-3 py-2 text-xs rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/30"><Loader2 size={14} className="animate-spin" /> {trainingStatus}</span>}
          <button onClick={fetchModels} className="p-2 rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }} title="Refresh"><RefreshCw size={16} style={{ color: "var(--muted)" }} /></button>
        </div>
      </div>

      {symbols.length > 0 && (
        <div className="flex gap-1 overflow-x-auto">
          <button onClick={() => setSymbolFilter("all")} className={"px-3 py-1.5 text-xs font-medium rounded-lg transition-colors " + (symbolFilter === "all" ? "bg-blue-600 text-white" : "hover:bg-white/10")} style={{ color: symbolFilter === "all" ? undefined : "var(--muted)" }}>All</button>
          {symbols.map((s) => <button key={s} onClick={() => setSymbolFilter(s)} className={"px-3 py-1.5 text-xs font-medium rounded-lg transition-colors " + (symbolFilter === s ? "bg-blue-600 text-white" : "hover:bg-white/10")} style={{ color: symbolFilter === s ? undefined : "var(--muted)" }}>{s}</button>)}
        </div>
      )}

      {models.length === 0 ? (
        <Card className="text-center py-12">
          <BrainCircuit size={40} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm mb-3" style={{ color: "var(--muted)" }}>No trained models yet</p>
          <div className="flex justify-center gap-2">
            {["XAUUSD", "BTCUSD", "US30"].map((s) => <button key={s} onClick={() => handleTrain(s)} disabled={training} className="px-3 py-2 text-xs rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">Train {s}</button>)}
          </div>
        </Card>
      ) : (
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
                        {m.grade && <StatusBadge value={m.grade} />}
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
      )}

      <div className="text-xs p-3 rounded-lg" style={{ background: "var(--sidebar-active)", color: "var(--muted)" }}>
        <span className="font-medium">Grade Criteria:</span> A=Sharpe&gt;1.5+WR&gt;55%+DD&lt;15% | B=Sharpe&gt;1.0+WR&gt;50%+DD&lt;20% | C=Sharpe&gt;0.5+WR&gt;45%+DD&lt;25% | D=Positive | F=Negative
      </div>

      <ModelDetailModal model={selectedModel} open={selectedModel !== null} onClose={() => setSelectedModel(null)} />
    </div>
  );
}
"""

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(CONTENT)
print(f"Written {len(CONTENT)} chars to {TARGET}")
