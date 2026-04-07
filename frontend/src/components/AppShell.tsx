"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import Sidebar from "./Sidebar";
import ProfileDropdown from "./ProfileDropdown";

const PUBLIC_PATHS = ["/login", "/register"];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    setAuthed(!!token);
    // Listen for storage changes (login/logout in other tabs)
    const handler = () => setAuthed(!!localStorage.getItem("access_token"));
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, [pathname]);

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
      <Sidebar />
      <div className="md:ml-16 min-h-screen">
        <header className="flex items-center justify-end px-4 py-3 md:px-6">
          <ProfileDropdown />
        </header>
        <main className="px-4 pb-6 md:px-6 pt-0">
          {children}
        </main>
      </div>
    </>
  );
}
