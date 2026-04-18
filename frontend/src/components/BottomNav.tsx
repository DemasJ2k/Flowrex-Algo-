"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  LineChart,
  Bot,
  MessageSquare,
  Settings,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Mobile bottom tab bar. 5 primary destinations, thumb-friendly.
 * Desktop hides this (sidebar handles nav).
 */
const TABS = [
  { href: "/", label: "Home", icon: LayoutDashboard },
  { href: "/trading", label: "Trading", icon: LineChart },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/ai", label: "AI", icon: MessageSquare },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

export default function BottomNav() {
  const pathname = usePathname();

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-40 md:hidden"
      style={{
        paddingBottom: "env(safe-area-inset-bottom, 0)",
      }}
      aria-label="Primary navigation"
    >
      <div
        className="glass border-t flex items-center justify-around px-2 py-1.5 rounded-none"
        style={{ borderColor: "var(--border)", borderRadius: 0 }}
      >
        {TABS.map((t) => {
          const Icon = t.icon;
          // /trading matches exactly; / matches only home (not prefix-match everything)
          const active = t.href === "/" ? pathname === "/" : pathname.startsWith(t.href);
          return (
            <Link
              key={t.href}
              href={t.href}
              className={cn(
                "relative flex flex-col items-center gap-0.5 rounded-lg px-3 py-1.5 min-w-[56px] min-h-[44px] transition-colors",
                "active:bg-white/5",
                active ? "text-white" : "text-[var(--muted)]"
              )}
              aria-current={active ? "page" : undefined}
            >
              {active && (
                <span
                  className="absolute top-0 left-1/2 -translate-x-1/2 w-6 h-0.5 rounded-full"
                  style={{
                    background: "linear-gradient(90deg, var(--accent-primary), var(--accent-secondary))",
                  }}
                />
              )}
              <Icon size={18} />
              <span className="text-[10px] font-medium">{t.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
