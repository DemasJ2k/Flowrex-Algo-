"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  LineChart,
  Bot,
  BrainCircuit,
  FlaskConical,
  Newspaper,
  Settings,
  ShieldCheck,
  MessageSquare,
  LifeBuoy,
  Menu,
  X,
  PanelLeftOpen,
  PanelLeftClose,
} from "lucide-react";

const navItems = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Trading", href: "/trading", icon: LineChart },
  { label: "Agents", href: "/agents", icon: Bot },
  { label: "Models", href: "/models", icon: BrainCircuit },
  { label: "Backtest", href: "/backtest", icon: FlaskConical },
  { label: "News", href: "/news", icon: Newspaper },
  { label: "AI Chat", href: "/ai", icon: MessageSquare },
  { label: "Help", href: "/help", icon: LifeBuoy },
  { label: "Settings", href: "/settings", icon: Settings },
  { label: "Admin", href: "/admin", icon: ShieldCheck, adminOnly: true },
] as const;

const COLLAPSED_W = "w-16";   // 64px
const EXPANDED_W = "w-56";    // 224px

export default function Sidebar() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);

  // Persist pin preference across reloads so user doesn't re-pin every visit.
  useEffect(() => {
    const stored = typeof window !== "undefined" ? localStorage.getItem("flowrex_sidebar_pinned") : null;
    if (stored === "true") setPinned(true);
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem("flowrex_sidebar_pinned", pinned ? "true" : "false");
      // Notify AppShell so it can adjust main content margin
      document.documentElement.setAttribute("data-sidebar-pinned", pinned ? "true" : "false");
    }
  }, [pinned]);

  useEffect(() => {
    import("@/lib/api").then(({ default: api }) => {
      api.get("/api/auth/me").then((r) => setIsAdmin(r.data?.is_admin || false)).catch(() => {});
    });
  }, []);

  const expanded = pinned;
  const visibleItems = navItems.filter((item) => !("adminOnly" in item && item.adminOnly) || isAdmin);

  return (
    <>
      {/* Mobile hamburger */}
      <button
        className="fixed top-4 left-4 z-50 md:hidden p-2 rounded-lg border"
        style={{ background: "var(--card)", borderColor: "var(--border)" }}
        onClick={() => setMobileOpen(true)}
      >
        <Menu size={20} />
      </button>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-black/60 md:hidden" onClick={() => setMobileOpen(false)} />
      )}

      {/* Mobile sidebar (full width overlay) */}
      <aside
        className={cn(
          "fixed left-0 top-0 h-screen w-56 flex flex-col border-r z-50 transition-transform duration-200 md:hidden",
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        )}
        style={{ background: "var(--sidebar-bg)", borderColor: "var(--border)" }}
      >
        {/* Logo */}
        <div className="flex items-center justify-between px-5 py-5 border-b" style={{ borderColor: "var(--border)" }}>
          <div className="flex items-center gap-2">
            <img src="/logo-icon.png" alt="FX" className="w-8 h-8 rounded-lg object-contain" />
            <span className="text-base font-semibold tracking-tight">Flowrex Algo</span>
          </div>
          <button className="p-1 rounded hover:bg-white/10" onClick={() => setMobileOpen(false)}>
            <X size={18} />
          </button>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1">
          {visibleItems.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link key={item.href} href={item.href} onClick={() => setMobileOpen(false)}
                className={cn("flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors", isActive ? "text-white" : "hover:text-white")}
                style={{ color: isActive ? "var(--foreground)" : "var(--muted)", background: isActive ? "var(--sidebar-active)" : "transparent" }}>
                <item.icon size={18} />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>

      {/* Desktop sidebar — click-to-pin. No hover-expand (was causing overlay
          issues on pages with their own sub-sidebars, e.g. AI chat). */}
      <aside
        className={cn(
          "fixed left-0 top-0 h-screen hidden md:flex flex-col border-r z-40 transition-all duration-200 overflow-hidden",
          expanded ? EXPANDED_W : COLLAPSED_W,
        )}
        style={{ background: "var(--sidebar-bg)", borderColor: "var(--border)" }}
      >
        {/* Logo + pin toggle */}
        <div className="flex items-center gap-2 px-4 py-4 border-b min-h-[64px]" style={{ borderColor: "var(--border)" }}>
          <img src="/logo-icon.png" alt="FX" className="w-8 h-8 rounded-lg flex-shrink-0 object-contain" />
          <span className={cn("flex-1 text-base font-semibold tracking-tight whitespace-nowrap transition-opacity duration-200", expanded ? "opacity-100" : "opacity-0 w-0 overflow-hidden")}>
            Flowrex Algo
          </span>
          <button
            onClick={() => setPinned(!pinned)}
            className="p-1 rounded hover:bg-white/10 flex-shrink-0"
            title={pinned ? "Collapse sidebar" : "Expand sidebar"}
            style={{ color: "var(--muted)" }}
          >
            {pinned ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-2 py-3 space-y-1">
          {visibleItems.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-lg text-sm font-medium transition-all duration-150 whitespace-nowrap",
                  expanded ? "px-3 py-2.5" : "px-3 py-2.5 justify-center",
                  isActive ? "text-white" : "hover:text-white"
                )}
                style={{
                  color: isActive ? "var(--foreground)" : "var(--muted)",
                  background: isActive ? "var(--sidebar-active)" : "transparent",
                }}
                onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = "var(--sidebar-hover)"; }}
                onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = isActive ? "var(--sidebar-active)" : "transparent"; }}
                title={expanded ? undefined : item.label}
              >
                <item.icon size={18} className="flex-shrink-0" />
                <span className={cn("transition-opacity duration-200", expanded ? "opacity-100" : "opacity-0 w-0 overflow-hidden")}>
                  {item.label}
                </span>
              </Link>
            );
          })}
        </nav>
      </aside>
    </>
  );
}
