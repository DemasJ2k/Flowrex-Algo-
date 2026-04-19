"use client";

import { useEffect } from "react";

/**
 * One-liner client-side service worker registration. Runs once on mount and
 * fails silently if the browser doesn't support SW (old iOS, etc.).
 */
export default function PwaRegister() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) return;
    // Only register in production — avoids SW stuck-state headaches in dev.
    if (window.location.hostname === "localhost") return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  }, []);
  return null;
}
