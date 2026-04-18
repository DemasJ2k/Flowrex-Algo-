"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Bot, Settings, Send, Trash2, MessageSquare, Plus, Clock, DollarSign, PanelLeftOpen, PanelLeftClose, Link as LinkIcon, Check, Copy, Unlink } from "lucide-react";
import Card from "@/components/ui/Card";
import api from "@/lib/api";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Message {
  id?: number;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  model?: string;
}

interface ChatSessionItem {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

interface LLMConfig {
  api_key_set: boolean;
  api_key_masked: string;
  model: string;
  enabled: boolean;
  autonomous: boolean;
  telegram_configured: boolean;
  telegram_chat_id: string;
}

interface UsageData {
  month: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
  sessions: number;
  messages: number;
}

const MODEL_OPTIONS = [
  { value: "haiku", label: "Claude Haiku 4.5", desc: "Fast & Cheap (~$5/mo)" },
  { value: "sonnet", label: "Claude Sonnet 4.5", desc: "Balanced (~$15/mo)" },
  { value: "opus", label: "Claude Opus 4", desc: "Best (~$30/mo)" },
];

export default function AIPage() {
  const [config, setConfig] = useState<LLMConfig | null>(null);
  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showSidebar, setShowSidebar] = useState(false);
  const [usage, setUsage] = useState<UsageData | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("haiku");
  const [enabled, setEnabled] = useState(false);
  const [autonomous, setAutonomous] = useState(false);

  const loadSessions = useCallback(async () => {
    try {
      const r = await api.get("/api/llm/sessions");
      setSessions(r.data);
    } catch { /* ignore */ }
  }, []);

  const loadSessionMessages = useCallback(async (sessionId: number) => {
    try {
      const r = await api.get(`/api/llm/sessions/${sessionId}`);
      setMessages(
        r.data.messages.map((m: { id: number; role: string; content: string; created_at: string; model?: string }) => ({
          id: m.id,
          role: m.role as "user" | "assistant",
          content: m.content,
          timestamp: m.created_at,
          model: m.model,
        }))
      );
      setActiveSessionId(sessionId);
    } catch {
      toast.error("Failed to load chat session");
    }
  }, []);

  useEffect(() => {
    api.get("/api/llm/config").then((r) => {
      const c = r.data;
      setConfig(c);
      setModel(c.model || "haiku");
      setEnabled(c.enabled);
      setAutonomous(c.autonomous);
    }).catch(() => {});

    loadSessions();
    api.get("/api/llm/usage").then((r) => setUsage(r.data)).catch(() => {});
  }, [loadSessions]);

  // Auto-load most recent session
  useEffect(() => {
    if (sessions.length > 0 && !activeSessionId) {
      loadSessionMessages(sessions[0].id);
    }
  }, [sessions, activeSessionId, loadSessionMessages]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const saveConfig = async () => {
    if (enabled && !config?.api_key_set && !apiKey) {
      toast.error("Enter your Anthropic API key before enabling the supervisor");
      return;
    }
    setSaving(true);
    try {
      const body: Record<string, unknown> = { model, enabled, autonomous };
      if (apiKey) body.api_key = apiKey;
      await api.post("/api/llm/config", body);
      toast.success("AI Supervisor settings saved");
      setApiKey("");
      const r = await api.get("/api/llm/config");
      setConfig(r.data);
      setShowSettings(false);
    } catch {
      toast.error("Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const newChat = async () => {
    setActiveSessionId(null);
    setMessages([]);
  };

  const deleteSession = async (sessionId: number) => {
    try {
      await api.delete(`/api/llm/sessions/${sessionId}`);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      if (activeSessionId === sessionId) {
        setActiveSessionId(null);
        setMessages([]);
      }
      toast.success("Chat deleted");
    } catch {
      toast.error("Failed to delete chat");
    }
  };

  const sendMessage = async () => {
    if (!input.trim() || loading) return;
    const userMsg: Message = { role: "user", content: input.trim(), timestamp: new Date().toISOString() };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    try {
      const body: Record<string, unknown> = { message: userMsg.content };
      if (activeSessionId) body.session_id = activeSessionId;
      const res = await api.post("/api/llm/chat", body, { timeout: 60000 });
      const newSessionId = res.data.session_id;

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.data.reply, timestamp: res.data.timestamp, model: res.data.model },
      ]);

      if (newSessionId) {
        setActiveSessionId(newSessionId);
        loadSessions();
      }
    } catch {
      toast.error("Failed to get AI response");
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateStr: string) => {
    if (!dateStr) return "";
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffDays = Math.floor(diffMs / 86400000);
    if (diffDays === 0) return "Today";
    if (diffDays === 1) return "Yesterday";
    if (diffDays < 7) return `${diffDays}d ago`;
    return d.toLocaleDateString();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot size={20} className="text-violet-400" />
          <h1 className="text-lg font-bold">AI Supervisor</h1>
        </div>
        <div className="flex items-center gap-2">
          {usage && (
            <span className="text-xs px-2 py-1 rounded-lg" style={{ background: "var(--card)", color: "var(--muted)" }}>
              <DollarSign size={10} className="inline mr-1" />
              ${usage.estimated_cost_usd.toFixed(2)} this month
            </span>
          )}
          <button
            onClick={() => setShowSettings(!showSettings)}
            className="p-2 rounded-lg hover:bg-white/5 transition-colors"
            title="Settings"
          >
            <Settings size={16} style={{ color: "var(--muted)" }} />
          </button>
        </div>
      </div>

      {/* Settings Modal */}
      {showSettings && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.6)" }}
          onClick={() => setShowSettings(false)}
        >
          <div
            className="relative w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-xl border shadow-2xl"
            style={{ background: "var(--card)", borderColor: "var(--border)" }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Sticky header */}
            <div className="sticky top-0 flex items-center justify-between px-5 py-3 border-b"
                 style={{ background: "var(--card)", borderColor: "var(--border)" }}>
              <div className="flex items-center gap-2">
                <Settings size={16} className="text-violet-400" />
                <h2 className="text-sm font-semibold">AI Supervisor Settings</h2>
              </div>
              <button onClick={() => setShowSettings(false)} className="p-1 rounded hover:bg-white/5">
                <span className="text-lg" style={{ color: "var(--muted)" }}>×</span>
              </button>
            </div>
            {/* Body */}
            <div className="p-5 grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="md:col-span-2">
                <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>
                  Anthropic API Key
                </label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={config?.api_key_set ? config.api_key_masked : "sk-ant-..."}
                  className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                  style={{ borderColor: "var(--border)", background: "var(--background)" }}
                />
                <p className="text-[10px] mt-1" style={{ color: "var(--muted)" }}>
                  Get your API key at{" "}
                  <a href="https://console.anthropic.com" target="_blank" rel="noopener noreferrer"
                    className="text-violet-400 hover:underline">console.anthropic.com</a>
                </p>
              </div>
              <div className="md:col-span-2">
                <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Model</label>
                <select value={model} onChange={(e) => setModel(e.target.value)}
                  className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                  style={{ borderColor: "var(--border)", background: "var(--background)" }}>
                  {MODEL_OPTIONS.map((m) => (
                    <option key={m.value} value={m.value}>{m.label} - {m.desc}</option>
                  ))}
                </select>
              </div>
              <div className="flex items-center justify-between p-3 rounded-lg" style={{ background: "var(--background)" }}>
                <span className="text-sm">Supervisor</span>
                <button onClick={() => setEnabled(!enabled)}
                  className={`w-10 h-5 rounded-full transition-colors relative ${enabled ? "bg-violet-600" : "bg-gray-600"}`}>
                  <div className={`w-4 h-4 rounded-full bg-white absolute top-0.5 transition-transform ${enabled ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </div>
              <div className="flex items-center justify-between p-3 rounded-lg" style={{ background: "var(--background)" }}>
                <div>
                  <span className="text-sm">Autonomous Mode</span>
                  <p className="text-[10px]" style={{ color: "var(--muted)" }}>Can pause/adjust risk</p>
                </div>
                <button onClick={() => setAutonomous(!autonomous)}
                  className={`w-10 h-5 rounded-full transition-colors relative ${autonomous ? "bg-violet-600" : "bg-gray-600"}`}>
                  <div className={`w-4 h-4 rounded-full bg-white absolute top-0.5 transition-transform ${autonomous ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </div>
              <div className="md:col-span-2 pt-3 mt-1" style={{ borderTop: "1px solid var(--border)" }}>
                <p className="text-xs font-semibold mb-2" style={{ color: "var(--muted)" }}>Telegram Notifications</p>
                <TelegramConnectCard />
              </div>
              <div className="md:col-span-2">
                <button onClick={saveConfig} disabled={saving}
                  className="w-full py-2 text-sm font-medium rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 transition-colors text-white">
                  {saving ? "Saving..." : "Save Settings"}
                </button>
              </div>
              {usage && (
                <div className="md:col-span-2 text-xs p-3 rounded-lg" style={{ background: "var(--background)", color: "var(--muted)" }}>
                  <strong className="text-violet-400">Monthly Usage:</strong> {usage.messages} messages across {usage.sessions} sessions |{" "}
                  {(usage.input_tokens / 1000).toFixed(1)}k input + {(usage.output_tokens / 1000).toFixed(1)}k output tokens |{" "}
                  Est. ${usage.estimated_cost_usd.toFixed(4)}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Main Chat Layout */}
      <div className="flex gap-2 md:gap-4 relative" style={{ height: "calc(100vh - 220px)" }}>
        {/* Session Sidebar — hidden on mobile unless toggled */}
        <div className={`${showSidebar ? "fixed inset-0 z-40 flex" : "hidden"} md:relative md:flex md:z-auto`}>
          {/* Backdrop on mobile */}
          {showSidebar && (
            <div className="fixed inset-0 bg-black/50 md:hidden" onClick={() => setShowSidebar(false)} />
          )}
          <div className="relative z-50 w-64 md:w-56 flex-shrink-0 flex flex-col rounded-xl border overflow-hidden"
            style={{ background: "var(--card)", borderColor: "var(--border)" }}>
            <button
              onClick={() => { newChat(); setShowSidebar(false); }}
              className="flex items-center gap-2 px-3 py-2.5 text-sm font-medium border-b hover:bg-white/5 transition-colors"
              style={{ borderColor: "var(--border)" }}
            >
              <Plus size={14} className="text-violet-400" />
              New Chat
            </button>
            <div className="flex-1 overflow-y-auto">
              {sessions.length === 0 && (
                <p className="text-xs text-center py-4" style={{ color: "var(--muted)" }}>No chats yet</p>
              )}
              {sessions.map((s) => (
                <div
                  key={s.id}
                  className={`group flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-white/5 transition-colors ${
                    activeSessionId === s.id ? "bg-violet-600/10 border-l-2 border-violet-500" : ""
                  }`}
                  onClick={() => { loadSessionMessages(s.id); setShowSidebar(false); }}
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium truncate">{s.title || "New Chat"}</p>
                    <p className="text-[10px]" style={{ color: "var(--muted)" }}>
                      <Clock size={9} className="inline mr-1" />
                      {formatDate(s.updated_at)} · {s.message_count} msgs
                    </p>
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); deleteSession(s.id); }}
                    className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-red-500/20 transition-all"
                    title="Delete"
                  >
                    <Trash2 size={12} style={{ color: "var(--muted)" }} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Chat Area */}
        <div className="flex-1 flex flex-col rounded-xl border overflow-hidden min-w-0"
          style={{ background: "var(--card)", borderColor: "var(--border)" }}>
          {/* Header */}
          <div className="flex items-center justify-between px-3 md:px-4 py-2.5 border-b"
            style={{ borderColor: "var(--border)" }}>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowSidebar(!showSidebar)}
                className="md:hidden p-1 rounded hover:bg-white/5"
              >
                {showSidebar ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
              </button>
              <MessageSquare size={14} className="text-violet-400" />
              <span className="text-sm font-medium truncate">
                {activeSessionId
                  ? sessions.find((s) => s.id === activeSessionId)?.title || "Chat"
                  : "New Chat"}
              </span>
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto space-y-3 p-3 md:p-4">
            {messages.length === 0 && (
              <div className="text-center py-12" style={{ color: "var(--muted)" }}>
                <Bot size={36} className="mx-auto mb-3 opacity-30" />
                <p className="text-sm">Ask about your trading performance</p>
                <p className="text-xs mt-1">e.g. &quot;How are my agents performing today?&quot;</p>
              </div>
            )}
            {messages.map((msg, i) => (
              <div key={msg.id || i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[90%] md:max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                  msg.role === "user" ? "bg-violet-600/20 text-violet-100" : ""
                }`} style={msg.role === "assistant" ? { background: "var(--card-hover)", border: "1px solid var(--border)" } : {}}>
                  {msg.role === "assistant" ? (
                    <div className="ai-md break-words">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {msg.content}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                  )}
                  <div className="text-[10px] mt-1 opacity-40">
                    {new Date(msg.timestamp).toLocaleTimeString()}
                    {msg.model && <span className="ml-2">{msg.model.split("-").slice(-1)[0]}</span>}
                  </div>
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex justify-start">
                <div className="rounded-lg px-3 py-2 text-sm animate-pulse"
                  style={{ background: "var(--card-hover)", border: "1px solid var(--border)", color: "var(--muted)" }}>
                  Thinking...
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input */}
          <div className="p-3 border-t" style={{ borderColor: "var(--border)" }}>
            <div className="flex gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
                placeholder="Ask about your trading..."
                className="flex-1 px-3 py-2 text-sm rounded-lg border bg-transparent"
                style={{ borderColor: "var(--border)", background: "var(--background)" }}
                disabled={loading}
              />
              <button onClick={sendMessage} disabled={!input.trim() || loading}
                className="px-3 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-40 transition-colors">
                <Send size={14} className="text-white" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}


interface TelegramStatus {
  connected: boolean;
  chat_id: string;
  telegram_username: string;
  telegram_first_name: string;
  global_bot_enabled: boolean;
  bot_username: string;
}

function TelegramConnectCard() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [deepLink, setDeepLink] = useState<string | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [expectedUsername, setExpectedUsername] = useState("");

  const loadStatus = useCallback(async () => {
    try {
      const r = await api.get("/api/telegram/status");
      setStatus(r.data);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    loadStatus();
    const t = setInterval(loadStatus, 10000);
    return () => clearInterval(t);
  }, [loadStatus]);

  const generateLink = async () => {
    setLoading(true);
    try {
      const r = await api.post("/api/telegram/connect");
      setDeepLink(r.data.deep_link);
      setCode(r.data.code);
    } catch {
      toast.error("Could not generate link. Is the bot configured?");
    } finally {
      setLoading(false);
    }
  };

  const disconnect = async () => {
    try {
      await api.post("/api/telegram/disconnect");
      toast.success("Disconnected from Telegram");
      setDeepLink(null);
      setCode(null);
      loadStatus();
    } catch {
      toast.error("Failed to disconnect");
    }
  };

  const copyCode = async () => {
    if (!code) return;
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!status) return <p className="text-xs" style={{ color: "var(--muted)" }}>Loading...</p>;

  if (!status.global_bot_enabled) {
    return (
      <p className="text-xs" style={{ color: "var(--muted)" }}>
        Central Telegram bot is not configured on this server.
      </p>
    );
  }

  if (status.connected) {
    const identity = status.telegram_username
      ? `@${status.telegram_username}`
      : status.telegram_first_name || `Chat #${status.chat_id}`;
    const mismatch = expectedUsername && status.telegram_username &&
      expectedUsername.toLowerCase() !== status.telegram_username.toLowerCase();
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between p-3 rounded-lg gap-3 flex-wrap" style={{ background: "var(--background)", border: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-2 h-2 rounded-full bg-emerald-500 flex-shrink-0" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-emerald-400 truncate">
                Connected as {identity}
              </p>
              <p className="text-[10px] truncate" style={{ color: "var(--muted)" }}>
                Chat ID: {status.chat_id}
                {status.telegram_first_name && ` · ${status.telegram_first_name}`}
              </p>
            </div>
          </div>
          <button
            onClick={disconnect}
            className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg border hover:bg-red-500/10 hover:border-red-500/50 transition-colors flex-shrink-0"
            style={{ borderColor: "var(--border)" }}
          >
            <Unlink size={12} /> Disconnect
          </button>
        </div>
        {mismatch && (
          <div className="p-2 rounded-lg text-xs" style={{ background: "rgba(245,158,11,0.1)", border: "1px solid rgba(245,158,11,0.3)", color: "#fcd34d" }}>
            ⚠ Expected <strong>@{expectedUsername}</strong> but connected as <strong>{identity}</strong>. Disconnect and reconnect if this is wrong.
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-xs" style={{ color: "var(--muted)" }}>
        Get trade alerts, hourly performance summaries, and AI-powered analysis sent to your Telegram.
      </p>
      {!deepLink ? (
        <div className="space-y-3">
          <div>
            <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--muted)" }}>
              Your Telegram Username <span className="opacity-60">(optional — for verification)</span>
            </label>
            <div className="flex items-center gap-2">
              <span className="text-sm" style={{ color: "var(--muted)" }}>@</span>
              <input
                value={expectedUsername}
                onChange={(e) => setExpectedUsername(e.target.value.replace(/^@/, ""))}
                placeholder="yourusername"
                className="flex-1 px-3 py-1.5 text-sm rounded-lg border bg-transparent"
                style={{ borderColor: "var(--border)", background: "var(--background)" }}
              />
            </div>
            <p className="text-[10px] mt-1" style={{ color: "var(--muted)" }}>
              After clicking the link, the bot will confirm which username it&apos;s linked to.
            </p>
          </div>
          <button
            onClick={generateLink}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 transition-colors text-white"
          >
            <LinkIcon size={14} />
            {loading ? "Generating..." : `Connect to @${status.bot_username}`}
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="p-3 rounded-lg" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
            <p className="text-xs mb-2" style={{ color: "var(--muted)" }}>
              <strong className="text-violet-400">Step 1:</strong> Open Telegram and click this link:
            </p>
            <a
              href={deepLink}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-sm text-blue-400 hover:text-blue-300 underline break-all"
            >
              {deepLink}
            </a>
            <p className="text-xs mt-3 mb-1" style={{ color: "var(--muted)" }}>
              <strong className="text-violet-400">Step 2:</strong> Or send <code className="px-1.5 py-0.5 rounded bg-violet-500/20 text-violet-300">/start {code}</code> to @{status.bot_username}
            </p>
            <div className="flex items-center gap-2 mt-2">
              <button
                onClick={copyCode}
                className="flex items-center gap-1 px-3 py-1 text-xs rounded-lg border hover:bg-white/5"
                style={{ borderColor: "var(--border)" }}
              >
                {copied ? <><Check size={12} className="text-emerald-400" /> Copied</> : <><Copy size={12} /> Copy code</>}
              </button>
              <span className="text-[10px]" style={{ color: "var(--muted)" }}>Code expires in 10 min</span>
            </div>
          </div>
          <button
            onClick={loadStatus}
            className="text-xs text-violet-400 hover:text-violet-300"
          >
            I've opened the link — refresh status
          </button>
        </div>
      )}
    </div>
  );
}
