/**
 * Timezone utility — all times displayed in Sydney (AEST/AEDT).
 * AEST = UTC+10, AEDT = UTC+11 (daylight saving)
 */

const TIMEZONE = "Australia/Sydney";

/** Format a date string or Date to Sydney time (HH:MM:SS) */
export function toSydneyTime(input: string | Date | number): string {
  const date = typeof input === "number"
    ? new Date(input > 1e12 ? input : input * 1000) // handle seconds vs ms
    : new Date(input);
  if (isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("en-AU", { timeZone: TIMEZONE, hour12: false });
}

/** Format a date string or Date to Sydney date (YYYY-MM-DD) */
export function toSydneyDate(input: string | Date | number): string {
  const date = typeof input === "number"
    ? new Date(input > 1e12 ? input : input * 1000)
    : new Date(input);
  if (isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("en-AU", { timeZone: TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit" });
}

/** Format a date string or Date to Sydney datetime (YYYY-MM-DD HH:MM) */
export function toSydneyDateTime(input: string | Date | number): string {
  const date = typeof input === "number"
    ? new Date(input > 1e12 ? input : input * 1000)
    : new Date(input);
  if (isNaN(date.getTime())) return "—";
  return date.toLocaleString("en-AU", {
    timeZone: TIMEZONE,
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

export { TIMEZONE };
