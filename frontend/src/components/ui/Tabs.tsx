"use client";

import { cn } from "@/lib/utils";
import { ReactNode, useState } from "react";

export interface TabItem {
  label: string;
  badge?: number;
  content: ReactNode;
}

export default function Tabs({
  tabs,
  defaultIndex = 0,
}: {
  tabs: TabItem[];
  defaultIndex?: number;
}) {
  const [active, setActive] = useState(defaultIndex);

  return (
    <div>
      {/* overflow-x-auto lets the tab row scroll horizontally on narrow
          viewports instead of overflowing the card. whitespace-nowrap keeps
          each tab label on a single line. */}
      <div
        className="flex border-b gap-0 overflow-x-auto scrollbar-thin"
        style={{ borderColor: "var(--border)" }}
      >
        {tabs.map((tab, i) => (
          <button
            key={i}
            onClick={() => setActive(i)}
            className={cn(
              "px-4 py-2.5 text-sm font-medium transition-colors relative whitespace-nowrap flex-shrink-0",
              active === i ? "text-white" : "hover:text-white"
            )}
            style={{ color: active === i ? "var(--foreground)" : "var(--muted)" }}
          >
            {tab.label}
            {tab.badge !== undefined && tab.badge > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 text-[10px] rounded-full bg-blue-500/20 text-blue-400">
                {tab.badge}
              </span>
            )}
            {active === i && (
              <div className="absolute bottom-0 left-0 right-0 h-0.5 rounded-t" style={{ background: "linear-gradient(to right, #8b5cf6, #3b82f6)" }} />
            )}
          </button>
        ))}
      </div>
      <div className="pt-4">{tabs[active]?.content}</div>
    </div>
  );
}
