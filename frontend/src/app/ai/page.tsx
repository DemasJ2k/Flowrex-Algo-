"use client";

import { useState, useEffect } from "react";
import { Bot, Settings, Send, Trash2, TestTube, MessageSquare } from "lucide-react";
import Card from "@/components/ui/Card";
import api from "@/lib/api";
import { toast } from "sonner";

interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
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

const MODEL_OPTIONS = [
  { value: "haiku", label: "Claude Haiku 4.5", desc: "Fast & Cheap (~$5/mo)" },
  { value: "sonnet", label: "Claude Sonnet 4.5", desc: "Balanced (~$15/mo)" },
  { value: "opus", label: "Claude Opus 4", desc: "Best (~$30/mo)" },
];

export default function AIPage() {
  const [config, setConfig] = useState<LLMConfig | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  // Config form state
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("haiku");
  const [enabled, setEnabled] = useState(false);
  const [autonomous, setAutonomous] = useState(false);
  const [telegramToken, setTelegramToken] = useState("");
  const [telegramChatId, setTelegramChatId] = useState("");

  useEffect(() => {
    api.get("/api/llm/config").then((r) => {
      const c = r.data;
      setConfig(c);
      setModel(c.model || "haiku");
      setEnabled(c.enabled);
      setAutonomous(c.autonomous);
      setTelegramChatId(c.telegram_chat_id || "");
    }).catch(() => {});
  }, []);

  const saveConfig = async () => {
    setSaving(true);
    try {
      const body: Record<string, unknown> = { model, enabled, autonomous };
      if (apiKey) body.api_key = apiKey;
      if (telegramToken) body.telegram_bot_token = telegramToken;
      if (telegramChatId) body.telegram_chat_id = telegramChatId;
      await api.post("/api/llm/config", body);
      toast.success("AI Supervisor settings saved");
      setApiKey("");
      setTelegramToken("");
      // Refresh config
      const r = await api.get("/api/llm/config");
      setConfig(r.data);
    } catch {
      toast.error("Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const sendMessage = async () => {
    if (!input.trim() || loading) return;
    const userMsg: Message = { role: "user", content: input.trim(), timestamp: new Date().toISOString() };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    try {
      const res = await api.post("/api/llm/chat", { message: userMsg.content });
      setMessages((prev) => [...prev, { role: "assistant", content: res.data.reply, timestamp: res.data.timestamp }]);
    } catch {
      toast.error("Failed to get AI response");
    } finally {
      setLoading(false);
    }
  };

  const clearChat = async () => {
    await api.post("/api/llm/chat/clear").catch(() => {});
    setMessages([]);
  };

  const testTelegram = async () => {
    try {
      await api.post("/api/llm/telegram/test");
      toast.success("Test message sent to Telegram");
    } catch {
      toast.error("Telegram test failed");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Bot size={20} className="text-violet-400" />
        <h1 className="text-lg font-bold">AI Supervisor</h1>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Settings Card */}
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <Settings size={16} style={{ color: "var(--muted)" }} />
            <h2 className="text-sm font-semibold">Configuration</h2>
          </div>
          <div className="space-y-4">
            {/* API Key */}
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>
                Anthropic API Key
              </label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={config?.api_key_set ? config.api_key_masked : "sk-ant-..."}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                style={{ borderColor: "var(--border)", background: "var(--card)" }}
              />
              <p className="text-[10px] mt-1" style={{ color: "var(--muted)" }}>
                Get your API key at{" "}
                <a href="https://console.anthropic.com" target="_blank" rel="noopener noreferrer"
                  className="text-violet-400 hover:underline">console.anthropic.com</a>
              </p>
            </div>

            {/* Model */}
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Model</label>
              <select value={model} onChange={(e) => setModel(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                {MODEL_OPTIONS.map((m) => (
                  <option key={m.value} value={m.value}>{m.label} - {m.desc}</option>
                ))}
              </select>
            </div>

            {/* Toggles */}
            <div className="flex items-center justify-between">
              <span className="text-sm">Supervisor</span>
              <button onClick={() => setEnabled(!enabled)}
                className={`w-10 h-5 rounded-full transition-colors relative ${enabled ? "bg-violet-600" : "bg-gray-600"}`}>
                <div className={`w-4 h-4 rounded-full bg-white absolute top-0.5 transition-transform ${enabled ? "translate-x-5" : "translate-x-0.5"}`} />
              </button>
            </div>
            <div className="flex items-center justify-between">
              <div>
                <span className="text-sm">Autonomous Mode</span>
                <p className="text-[10px]" style={{ color: "var(--muted)" }}>Can pause/stop agents</p>
              </div>
              <button onClick={() => setAutonomous(!autonomous)}
                className={`w-10 h-5 rounded-full transition-colors relative ${autonomous ? "bg-violet-600" : "bg-gray-600"}`}>
                <div className={`w-4 h-4 rounded-full bg-white absolute top-0.5 transition-transform ${autonomous ? "translate-x-5" : "translate-x-0.5"}`} />
              </button>
            </div>

            {/* Telegram */}
            <div className="pt-2" style={{ borderTop: "1px solid var(--border)" }}>
              <p className="text-xs font-medium mb-2" style={{ color: "var(--muted)" }}>Telegram Notifications</p>
              <input
                type="password"
                value={telegramToken}
                onChange={(e) => setTelegramToken(e.target.value)}
                placeholder={config?.telegram_configured ? "Bot token configured" : "Bot Token"}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent mb-2"
                style={{ borderColor: "var(--border)", background: "var(--card)" }}
              />
              <input
                value={telegramChatId}
                onChange={(e) => setTelegramChatId(e.target.value)}
                placeholder="Chat ID"
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                style={{ borderColor: "var(--border)", background: "var(--card)" }}
              />
              <div className="flex gap-2 mt-2">
                <button onClick={testTelegram}
                  className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg border hover:bg-white/5"
                  style={{ borderColor: "var(--border)" }}>
                  <TestTube size={12} /> Test
                </button>
              </div>
            </div>

            {/* Save */}
            <button onClick={saveConfig} disabled={saving}
              className="w-full py-2 text-sm font-medium rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 transition-colors text-white">
              {saving ? "Saving..." : "Save AI Settings"}
            </button>
          </div>
        </Card>

        {/* Chat Card */}
        <Card className="flex flex-col min-h-[500px]">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <MessageSquare size={16} className="text-violet-400" />
              <h2 className="text-sm font-semibold">Chat</h2>
            </div>
            <button onClick={clearChat} className="p-1 rounded hover:bg-white/5" title="Clear">
              <Trash2 size={14} style={{ color: "var(--muted)" }} />
            </button>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto space-y-3 mb-3 pr-1" style={{ maxHeight: "400px" }}>
            {messages.length === 0 && (
              <div className="text-center py-8" style={{ color: "var(--muted)" }}>
                <Bot size={32} className="mx-auto mb-2 opacity-30" />
                <p className="text-sm">Ask about your trading performance</p>
                <p className="text-xs mt-1">e.g. &quot;How are my agents performing today?&quot;</p>
              </div>
            )}
            {messages.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                  msg.role === "user" ? "bg-violet-600/20 text-violet-100" : ""
                }`} style={msg.role === "assistant" ? { background: "var(--card-hover)", border: "1px solid var(--border)" } : {}}>
                  <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                  <div className="text-[10px] mt-1 opacity-40">{new Date(msg.timestamp).toLocaleTimeString()}</div>
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
          </div>

          {/* Input */}
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
              placeholder="Ask about your trading..."
              className="flex-1 px-3 py-2 text-sm rounded-lg border bg-transparent"
              style={{ borderColor: "var(--border)", background: "var(--card)" }}
              disabled={loading}
            />
            <button onClick={sendMessage} disabled={!input.trim() || loading}
              className="px-3 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-40 transition-colors">
              <Send size={14} className="text-white" />
            </button>
          </div>
        </Card>
      </div>
    </div>
  );
}
