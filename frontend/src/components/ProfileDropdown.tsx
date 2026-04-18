"use client";

import { debugWarn } from "@/lib/debug";
import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { Settings, LogOut, Shield, User, ChevronDown } from "lucide-react";
import api from "@/lib/api";

export default function ProfileDropdown() {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();

  useEffect(() => {
    api.get("/api/auth/me").then((r) => {
      setEmail(r.data.email || "");
      setIsAdmin(r.data.is_admin || false);
    }).catch((e) => debugWarn("fetch failed:", e?.message));
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const initials = email ? email.slice(0, 2).toUpperCase() : "U";

  const handleLogout = () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    router.push("/login");
  };

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg transition-colors hover:bg-white/5"
      >
        <div
          className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white"
          style={{ background: "linear-gradient(135deg, #8b5cf6, #3b82f6)" }}
        >
          {initials}
        </div>
        <span className="text-sm hidden sm:inline" style={{ color: "var(--muted)" }}>
          {email || "Account"}
        </span>
        <ChevronDown size={14} style={{ color: "var(--muted)" }} />
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1 w-48 rounded-lg border shadow-xl overflow-hidden z-50"
          style={{ background: "var(--card)", borderColor: "var(--border)" }}
        >
          <div className="px-3 py-2 border-b" style={{ borderColor: "var(--border)" }}>
            <p className="text-xs font-medium truncate">{email}</p>
            {isAdmin && (
              <span className="inline-flex items-center gap-1 text-[10px] mt-1 px-1.5 py-0.5 rounded-full" style={{ background: "rgba(139,92,246,0.15)", color: "#a78bfa" }}>
                <Shield size={10} /> Admin
              </span>
            )}
          </div>
          <button
            onClick={() => { setOpen(false); router.push("/settings"); }}
            className="w-full px-3 py-2 text-sm text-left flex items-center gap-2 hover:bg-white/5 transition-colors"
          >
            <Settings size={14} style={{ color: "var(--muted)" }} /> Settings
          </button>
          <button
            onClick={handleLogout}
            className="w-full px-3 py-2 text-sm text-left flex items-center gap-2 hover:bg-white/5 transition-colors text-red-400"
          >
            <LogOut size={14} /> Log out
          </button>
        </div>
      )}
    </div>
  );
}
