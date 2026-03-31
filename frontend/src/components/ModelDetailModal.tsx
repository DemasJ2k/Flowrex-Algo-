"use client";

import Modal from "@/components/ui/Modal";
import { StatCard } from "@/components/ui/Card";
import StatusBadge from "@/components/ui/StatusBadge";
import type { MLModel } from "@/types";

export default function ModelDetailModal({
  model,
  open,
  onClose,
}: {
  model: MLModel | null;
  open: boolean;
  onClose: () => void;
}) {
  if (!model) return null;

  const m = model.metrics || {};
  const fmt = (v: number | undefined) => v !== undefined ? v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "\u2014";

  // Feature importance (if available in metrics)
  const importances: [string, number][] = Object.entries(m)
    .filter(([k]) => !["accuracy", "sharpe", "win_rate", "max_drawdown", "total_return", "profit_factor", "total_trades"].includes(k))
    .sort(([, a], [, b]) => (b as number) - (a as number))
    .slice(0, 20) as [string, number][];

  const maxImportance = importances.length > 0 ? Math.max(...importances.map(([, v]) => v)) : 1;

  return (
    <Modal open={open} onClose={onClose} title={`${model.symbol} ${model.model_type}`} width="max-w-2xl">
      {/* Header */}
      <div className="flex items-center gap-2 mb-4">
        <StatusBadge value={model.pipeline} />
        <StatusBadge value={model.model_type} />
        {model.grade && <StatusBadge value={model.grade} />}
        <span className="text-xs" style={{ color: "var(--muted)" }}>
          Trained: {model.trained_at ? new Date(model.trained_at).toLocaleDateString() : "Unknown"}
        </span>
      </div>

      {/* Metrics Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
        <StatCard label="Accuracy" value={m.accuracy ? (m.accuracy * 100).toFixed(1) + "%" : "\u2014"} />
        <StatCard label="Sharpe Ratio" value={fmt(m.sharpe)} />
        <StatCard label="Win Rate" value={m.win_rate ? m.win_rate.toFixed(1) + "%" : "\u2014"} />
        <StatCard label="Max Drawdown" value={fmt(m.max_drawdown)} color="red" />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
        <StatCard label="Profit Factor" value={fmt(m.profit_factor)} />
        <StatCard label="Total Return" value={m.total_return ? m.total_return.toFixed(2) + "%" : "\u2014"} />
        <StatCard label="Total Trades" value={m.total_trades || "\u2014"} />
        <StatCard label="Grade" value={model.grade || "N/A"} />
      </div>

      {/* Grade Legend */}
      <div className="text-xs mb-4 p-3 rounded-lg" style={{ background: "var(--sidebar-active)", color: "var(--muted)" }}>
        <span className="font-medium">Grade Criteria:</span>{" "}
        <span className="text-emerald-400">A</span>=Sharpe&gt;1.5+WR&gt;55%+DD&lt;15% |{" "}
        <span className="text-blue-400">B</span>=Sharpe&gt;1.0+WR&gt;50%+DD&lt;20% |{" "}
        <span className="text-amber-400">C</span>=Sharpe&gt;0.5+WR&gt;45%+DD&lt;25% |{" "}
        <span className="text-orange-400">D</span>=Positive |{" "}
        <span className="text-red-400">F</span>=Negative
      </div>

      {/* Model Info */}
      <div className="text-xs space-y-1 mb-4" style={{ color: "var(--muted)" }}>
        <div className="flex justify-between"><span>Symbol</span><span className="text-white">{model.symbol}</span></div>
        <div className="flex justify-between"><span>Timeframe</span><span className="text-white">{model.timeframe}</span></div>
        <div className="flex justify-between"><span>Model Type</span><span className="text-white">{model.model_type}</span></div>
        <div className="flex justify-between"><span>Pipeline</span><span className="text-white">{model.pipeline}</span></div>
      </div>

      {/* Feature Importance (if training stored importance in metrics) */}
      {importances.length > 0 && (
        <div>
          <h3 className="text-sm font-medium mb-2">Top Features</h3>
          <div className="space-y-1 max-h-64 overflow-y-auto">
            {importances.map(([name, value]) => (
              <div key={name} className="flex items-center gap-2 text-xs">
                <span className="w-40 truncate" style={{ color: "var(--muted)" }}>{name}</span>
                <div className="flex-1 h-3 rounded-full overflow-hidden" style={{ background: "var(--sidebar-active)" }}>
                  <div className="h-full rounded-full bg-blue-500" style={{ width: `${(value / maxImportance) * 100}%` }} />
                </div>
                <span className="w-12 text-right" style={{ color: "var(--muted)" }}>{(value * 100).toFixed(1)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Modal>
  );
}
