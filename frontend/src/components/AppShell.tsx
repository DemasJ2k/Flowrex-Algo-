"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import Sidebar from "./Sidebar";
import ProfileDropdown from "./ProfileDropdown";

const PUBLIC_PATHS = ["/login", "/register"];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [sidebarPinned, setSidebarPinned] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    setAuthed(!!token);
    const handler = () => setAuthed(!!localStorage.getItem("access_token"));
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, [pathname]);

  // Track sidebar pin state so main content shifts to avoid overlap.
  useEffect(() => {
    const stored = localStorage.getItem("flowrex_sidebar_pinned") === "true";
    setSidebarPinned(stored);
    // Poll document attribute — Sidebar updates it on toggle
    const observer = new MutationObserver(() => {
      setSidebarPinned(document.documentElement.getAttribute("data-sidebar-pinned") === "true");
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-sidebar-pinned"] });
    return () => observer.disconnect();
  }, []);

  // Still loading auth state
  if (authed === null) return <>{children}</>;

  const isPublicPage = PUBLIC_PATHS.includes(pathname);
  const isLanding = pathname === "/" && !authed;

  // Public pages (login, register) and landing page: no sidebar
  if (isPublicPage || isLanding) {
    return <>{children}</>;
  }

  // Authenticated pages: sidebar + profile dropdown
  return (
    <>
      {/* Skip-to-content link for keyboard users — bypasses the sidebar nav */}
      <a href="#main-content" className="skip-link">Skip to main content</a>
      <Sidebar />
      <div className={`min-h-screen transition-all duration-200 ${sidebarPinned ? "md:ml-56" : "md:ml-16"}`}>
        <header className="flex items-center justify-end px-4 py-3 md:px-6">
          <ProfileDropdown />
        </header>
        <main id="main-content" className="px-4 pb-6 md:px-6 pt-0" tabIndex={-1}>
          {children}
        </main>
      </div>
    </>
  );
}
