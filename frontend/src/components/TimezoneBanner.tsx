"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import api from "@/lib/api";
import {
  detectBrowserTimezone,
  getUserTimezone,
  isTimezoneConfirmed,
  setUserTimezone,
} from "@/lib/timezone";

/**
 * One-shot banner that surfaces the browser-detected timezone the first time a
 * logged-in user lands on the app. Fetches the authoritative value from the
 * backend, and if the user hasn't confirmed one yet, prompts them once.
 *
 * Dismissal (Keep / Change) persists via UserSettings.settings_json.timezone
 * and localStorage so the banner never reappears after that.
 */
export default function TimezoneBanner() {
  const [visible, setVisible] = useState(false);
  const [detected, setDetected] = useState<string>("UTC");

  useEffect(() => {
    const token = typeof window !== "undefined"
      ? window.localStorage.getItem("access_token") : null;
    if (!token) return;

    let cancelled = false;
    (async () => {
      try {
        const res = await api.get("/api/llm/timezone");
        const backendTz: string = res.data?.timezone || "UTC";
        const confirmed: boolean = !!res.data?.confirmed;
        const browserTz = detectBrowserTimezone();

        // Seed local storage so formatters use the right zone immediately.
        setUserTimezone(backendTz, confirmed);

        if (cancelled) return;

        // Only prompt if the user has never confirmed AND the browser differs
        // from whatever the backend currently has. Otherwise stay silent.
        if (!confirmed && browserTz && browserTz !== backendTz) {
          setDetected(browserTz);
          setVisible(true);
        }
      } catch {
        // Fall back to browser zone locally; backend will catch up later.
        const browserTz = detectBrowserTimezone();
        setUserTimezone(browserTz, isTimezoneConfirmed());
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  if (!visible) return null;

  const keep = async () => {
    try {
      await api.put("/api/llm/timezone", { timezone: detected, confirmed: true });
      setUserTimezone(detected, true);
      toast.success(`Timezone set to ${detected}`);
    } catch {
      toast.error("Could not save timezone — try again from Settings.");
    } finally {
      setVisible(false);
    }
  };

  const change = () => {
    setVisible(false);
    // Settings hash-jumps to the Timezone row so the user can pick.
    if (typeof window !== "undefined") {
      window.location.hash = "#timezone";
      window.location.pathname = "/settings";
    }
  };

  return (
    <div className="fixed top-2 left-1/2 -translate-x-1/2 z-50 max-w-md w-[calc(100%-1rem)]">
      <div
        className="glass flex items-center gap-3 px-4 py-3 rounded-xl text-sm"
        style={{ borderColor: "var(--border)" }}
      >
        <span className="text-[var(--muted)]">🌐</span>
        <div className="flex-1 leading-tight">
          <div className="font-medium">We detected your timezone as</div>
          <div className="text-[var(--muted)]">{detected}</div>
        </div>
        <button
          onClick={change}
          className="px-3 py-1.5 rounded-lg text-xs border hover:bg-white/5"
          style={{ borderColor: "var(--border)" }}
        >
          Change
        </button>
        <button
          onClick={keep}
          className="px-3 py-1.5 rounded-lg text-xs font-medium text-black"
          style={{ background: "linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))" }}
        >
          Keep
        </button>
      </div>
    </div>
  );
}
