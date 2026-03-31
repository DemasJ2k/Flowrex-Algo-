"use client";

import type { WSStatus } from "@/hooks/useWebSocket";

const STATUS_CONFIG: Record<WSStatus, { color: string; bg: string; label: string }> = {
  connected:    { color: "text-emerald-400", bg: "bg-emerald-400", label: "Live" },
  reconnecting: { color: "text-amber-400",   bg: "bg-amber-400",   label: "Reconnecting..." },
  disconnected: { color: "text-red-400",      bg: "bg-red-400",     label: "Offline" },
};

export default function WSStatusBadge({ status }: { status: WSStatus }) {
  const config = STATUS_CONFIG[status];
  return (
    <span className={`flex items-center gap-1.5 text-xs ${config.color}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${config.bg} ${status === "reconnecting" ? "animate-pulse" : ""}`} />
      {config.label}
    </span>
  );
}
