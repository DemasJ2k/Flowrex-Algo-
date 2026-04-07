"use client";

import { useEffect, useState, useCallback } from "react";
import api from "@/lib/api";
import Card from "@/components/ui/Card";
import Tabs from "@/components/ui/Tabs";
import { Newspaper, RefreshCw, ExternalLink, Clock, Filter } from "lucide-react";

interface CalendarEvent {
  event: string;
  country: string;
  impact: string;
  actual: number | null;
  estimate: number | null;
  previous: number | null;
  time: string;
  date: string;
  unit: string;
  currency: string;
}

interface NewsArticle {
  headline: string;
  source: string;
  url: string;
  datetime: number;
  image: string;
  summary: string;
  category: string;
}

type ImpactFilter = "all" | "high" | "us";

function timeAgo(unix: number): string {
  const diff = Math.floor(Date.now() / 1000) - unix;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function ImpactBadge({ impact }: { impact: string }) {
  const level = impact?.toLowerCase() || "low";
  const colors: Record<string, string> = {
    high: "bg-red-500/15 text-red-400 border-red-500/30",
    medium: "bg-amber-500/15 text-amber-400 border-amber-500/30",
    low: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  };
  const cls = colors[level] || colors.low;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border ${cls}`}>
      {level.toUpperCase()}
    </span>
  );
}

function CalendarSection() {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<ImpactFilter>("all");

  const fetchCalendar = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params: Record<string, string> = {};
      if (filter === "high") params.impact = "high";
      if (filter === "us") params.country = "US";
      const res = await api.get("/api/news/calendar", { params });
      setEvents(res.data.events || []);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Failed to load calendar");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    fetchCalendar();
    const interval = setInterval(fetchCalendar, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [fetchCalendar]);

  const filterButtons: { label: string; value: ImpactFilter }[] = [
    { label: "All", value: "all" },
    { label: "High Impact", value: "high" },
    { label: "US Only", value: "us" },
  ];

  return (
    <div>
      {/* Filter bar */}
      <div className="flex items-center gap-2 mb-4">
        <Filter size={14} style={{ color: "var(--muted)" }} />
        {filterButtons.map((btn) => (
          <button
            key={btn.value}
            onClick={() => setFilter(btn.value)}
            className="px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors"
            style={{
              background: filter === btn.value ? "var(--accent)" : "transparent",
              borderColor: filter === btn.value ? "var(--accent)" : "var(--border)",
              color: filter === btn.value ? "#fff" : "var(--muted)",
            }}
          >
            {btn.label}
          </button>
        ))}
      </div>

      {loading && (
        <div className="flex items-center justify-center py-12" style={{ color: "var(--muted)" }}>
          <RefreshCw size={16} className="animate-spin mr-2" /> Loading calendar...
        </div>
      )}

      {error && (
        <Card className="text-center py-8">
          <p className="text-red-400 text-sm">{error}</p>
        </Card>
      )}

      {!loading && !error && events.length === 0 && (
        <Card className="text-center py-8">
          <p className="text-sm" style={{ color: "var(--muted)" }}>No calendar events found for this filter.</p>
        </Card>
      )}

      {!loading && !error && events.length > 0 && (
        <div className="overflow-x-auto rounded-xl border" style={{ borderColor: "var(--border)" }}>
          <table className="w-full text-sm">
            <thead>
              <tr style={{ background: "var(--card)" }}>
                {["Time", "Event", "Country", "Impact", "Actual", "Estimate", "Previous"].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider"
                    style={{ color: "var(--muted)", borderBottom: "1px solid var(--border)" }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {events.map((ev, i) => (
                <tr
                  key={i}
                  className="transition-colors"
                  style={{ borderBottom: "1px solid var(--border)" }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--sidebar-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  <td className="px-4 py-3 whitespace-nowrap" style={{ color: "var(--muted)" }}>
                    <div className="flex items-center gap-1.5">
                      <Clock size={12} />
                      <span>{ev.date} {ev.time}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 font-medium text-white max-w-[300px] truncate">{ev.event}</td>
                  <td className="px-4 py-3">
                    <span className="px-2 py-0.5 text-xs rounded border" style={{ borderColor: "var(--border)", color: "var(--muted)" }}>
                      {ev.country}
                    </span>
                  </td>
                  <td className="px-4 py-3"><ImpactBadge impact={ev.impact} /></td>
                  <td className="px-4 py-3 font-mono text-xs" style={{ color: ev.actual !== null ? "#fff" : "var(--muted)" }}>
                    {ev.actual !== null ? ev.actual : "--"}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs" style={{ color: "var(--muted)" }}>
                    {ev.estimate !== null ? ev.estimate : "--"}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs" style={{ color: "var(--muted)" }}>
                    {ev.previous !== null ? ev.previous : "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function HeadlinesSection() {
  const [articles, setArticles] = useState<NewsArticle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchHeadlines = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.get("/api/news/headlines");
      setArticles(res.data.articles || []);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Failed to load headlines");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHeadlines();
    const interval = setInterval(fetchHeadlines, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [fetchHeadlines]);

  return (
    <div>
      {loading && (
        <div className="flex items-center justify-center py-12" style={{ color: "var(--muted)" }}>
          <RefreshCw size={16} className="animate-spin mr-2" /> Loading headlines...
        </div>
      )}

      {error && (
        <Card className="text-center py-8">
          <p className="text-red-400 text-sm">{error}</p>
        </Card>
      )}

      {!loading && !error && articles.length === 0 && (
        <Card className="text-center py-8">
          <p className="text-sm" style={{ color: "var(--muted)" }}>No headlines available.</p>
        </Card>
      )}

      {!loading && !error && articles.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {articles.map((article, i) => (
            <Card key={i} className="flex flex-col gap-3 hover:border-blue-500/30 transition-colors">
              {article.image && (
                <div className="w-full h-36 rounded-lg overflow-hidden bg-black/20">
                  <img
                    src={article.image}
                    alt=""
                    className="w-full h-full object-cover"
                    onError={(e) => (e.currentTarget.style.display = "none")}
                  />
                </div>
              )}
              <div className="flex-1 flex flex-col gap-2">
                <h3 className="text-sm font-semibold text-white leading-snug line-clamp-2">
                  {article.headline}
                </h3>
                {article.summary && (
                  <p className="text-xs leading-relaxed line-clamp-2" style={{ color: "var(--muted)" }}>
                    {article.summary}
                  </p>
                )}
                <div className="mt-auto flex items-center justify-between pt-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium" style={{ color: "var(--accent)" }}>{article.source}</span>
                    <span className="text-xs" style={{ color: "var(--muted)" }}>{timeAgo(article.datetime)}</span>
                  </div>
                  {article.url && (
                    <a
                      href={article.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 text-xs font-medium transition-colors hover:text-white"
                      style={{ color: "var(--muted)" }}
                    >
                      Read <ExternalLink size={12} />
                    </a>
                  )}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

export default function NewsPage() {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg" style={{ background: "var(--accent)" }}>
          <Newspaper size={20} className="text-white" />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-white">News & Calendar</h1>
          <p className="text-xs" style={{ color: "var(--muted)" }}>
            Economic events and market headlines -- auto-refreshes every 5 minutes
          </p>
        </div>
      </div>

      {/* Tabs */}
      <Tabs
        tabs={[
          { label: "Market Headlines", content: <HeadlinesSection /> },
          { label: "Economic Calendar", content: <CalendarSection /> },
        ]}
      />
    </div>
  );
}
