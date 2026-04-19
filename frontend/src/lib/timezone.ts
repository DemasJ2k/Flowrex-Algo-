/**
 * Timezone utility — formats times in the user's configured timezone.
 *
 * The timezone is resolved in this order:
 *   1. Value persisted in localStorage ("flowrex_user_tz") — synced from backend
 *      on login via fetchUserTimezone() in AppShell.
 *   2. Browser autodetection via Intl.DateTimeFormat().resolvedOptions().timeZone.
 *   3. "UTC" as a last resort.
 *
 * Backend stores the authoritative value in UserSettings.settings_json.timezone;
 * frontend mirrors it so formatting works synchronously in the render path.
 */

const STORAGE_KEY = "flowrex_user_tz";
const CONFIRMED_KEY = "flowrex_user_tz_confirmed";

export function detectBrowserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function getUserTimezone(): string {
  if (typeof window === "undefined") return "UTC";
  const saved = window.localStorage.getItem(STORAGE_KEY);
  if (saved) return saved;
  return detectBrowserTimezone();
}

export function setUserTimezone(tz: string, confirmed: boolean) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, tz);
  window.localStorage.setItem(CONFIRMED_KEY, confirmed ? "true" : "false");
  // Notify components that read the value on mount
  window.dispatchEvent(new Event("flowrex-tz-changed"));
}

export function isTimezoneConfirmed(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(CONFIRMED_KEY) === "true";
}

function coerceDate(input: string | Date | number): Date {
  return typeof input === "number"
    ? new Date(input > 1e12 ? input : input * 1000) // handle seconds vs ms
    : new Date(input);
}

/** Format a date to the user's timezone (HH:MM:SS) */
export function toSydneyTime(input: string | Date | number): string {
  const date = coerceDate(input);
  if (isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("en-GB", { timeZone: getUserTimezone(), hour12: false });
}

/** Format a date to the user's timezone (YYYY-MM-DD) */
export function toSydneyDate(input: string | Date | number): string {
  const date = coerceDate(input);
  if (isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("en-CA", {
    timeZone: getUserTimezone(),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

/** Format a date to the user's timezone (YYYY-MM-DD HH:MM) */
export function toSydneyDateTime(input: string | Date | number): string {
  const date = coerceDate(input);
  if (isNaN(date.getTime())) return "—";
  return date.toLocaleString("en-GB", {
    timeZone: getUserTimezone(),
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/** Format relative time ago */
export function timeAgo(input: string | number): string {
  const ts = typeof input === "number" ? input : new Date(input).getTime() / 1000;
  const diff = Math.floor(Date.now() / 1000) - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// Legacy export kept so older imports don't break; now resolves dynamically.
export const TIMEZONE = typeof window === "undefined" ? "UTC" : getUserTimezone();
