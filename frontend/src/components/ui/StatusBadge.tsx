"use client";

import { cn } from "@/lib/utils";

const VARIANTS: Record<string, string> = {
  running:  "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  stopped:  "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
  paused:   "bg-amber-500/15 text-amber-400 border-amber-500/30",
  error:    "bg-red-500/15 text-red-400 border-red-500/30",
  buy:      "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  sell:     "bg-red-500/15 text-red-400 border-red-500/30",
  open:     "bg-blue-500/15 text-blue-400 border-blue-500/30",
  closed:   "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
  pending:  "bg-amber-500/15 text-amber-400 border-amber-500/30",
  info:     "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
  warn:     "bg-amber-500/15 text-amber-400 border-amber-500/30",
  signal:   "bg-blue-500/15 text-blue-400 border-blue-500/30",
  trade:    "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  scalping: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  expert:   "bg-purple-500/15 text-purple-400 border-purple-500/30",
  flowrex:  "bg-indigo-500/15 text-indigo-400 border-indigo-500/30",
  paper:    "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
  live:     "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  // Grades
  A: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  B: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  C: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  D: "bg-orange-500/15 text-orange-400 border-orange-500/30",
  F: "bg-red-500/15 text-red-400 border-red-500/30",
};

export default function StatusBadge({ value, className }: { value: string; className?: string }) {
  const variant = VARIANTS[value.toLowerCase()] || VARIANTS.info;
  return (
    <span className={cn("inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border", variant, className)}>
      {value.toUpperCase()}
    </span>
  );
}
