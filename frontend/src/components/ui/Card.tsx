"use client";

import { cn } from "@/lib/utils";
import { ReactNode } from "react";

export default function Card({
  children,
  className,
  glow,
}: {
  children: ReactNode;
  className?: string;
  glow?: boolean;
}) {
  return (
    <div
      className={cn("rounded-xl border p-4 card-hover-glow", glow && "hover:shadow-[0_0_20px_rgba(139,92,246,0.1)]", className)}
      style={{ background: "var(--card)", borderColor: "var(--border)" }}
    >
      {children}
    </div>
  );
}

export function StatCard({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: "green" | "red" | "default";
}) {
  const valueColor =
    color === "green"
      ? "text-emerald-400"
      : color === "red"
      ? "text-red-400"
      : "text-white";
  return (
    <Card className="min-w-[160px]">
      <p className="text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>
        {label}
      </p>
      <p className={cn("text-xl font-semibold", valueColor)}>{value}</p>
      {sub && (
        <p className="text-xs mt-0.5" style={{ color: "var(--muted)" }}>
          {sub}
        </p>
      )}
    </Card>
  );
}
