"use client";

import { useState, useRef, useEffect } from "react";
import { X, Send, Trash2, Bot } from "lucide-react";
import api from "@/lib/api";
import { toast } from "sonner";

interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

interface LLMChatPanelProps {
  open: boolean;
  onClose: () => void;
}

export default function LLMChatPanel({ open, onClose }: LLMChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const sendMessage = async () => {
    if (!input.trim() || loading) return;

    const userMsg: Message = {
      role: "user",
      content: input.trim(),
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const res = await api.post("/api/llm/chat", { message: userMsg.content });
      const assistantMsg: Message = {
        role: "assistant",
        content: res.data.reply,
        timestamp: res.data.timestamp,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch {
      toast.error("Failed to get AI response. Check your API key in Settings.");
    } finally {
      setLoading(false);
    }
  };

  const clearChat = async () => {
    try {
      await api.post("/api/llm/chat/clear");
      setMessages([]);
    } catch {
      setMessages([]);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/30 z-40"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="fixed right-0 top-0 h-full w-[400px] max-w-[90vw] z-50 flex flex-col"
        style={{ background: "var(--bg)", borderLeft: "1px solid var(--border)" }}>

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2">
            <Bot size={18} className="text-violet-400" />
            <span className="font-semibold text-sm">AI Supervisor</span>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={clearChat}
              className="p-1.5 rounded-lg hover:bg-white/5 transition-colors"
              title="Clear chat">
              <Trash2 size={14} style={{ color: "var(--muted)" }} />
            </button>
            <button onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-white/5 transition-colors">
              <X size={16} style={{ color: "var(--muted)" }} />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {messages.length === 0 && (
            <div className="text-center py-8" style={{ color: "var(--muted)" }}>
              <Bot size={32} className="mx-auto mb-2 opacity-30" />
              <p className="text-sm">Ask me about your trading performance.</p>
              <p className="text-xs mt-1">e.g. &quot;Why did US30 lose money today?&quot;</p>
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                msg.role === "user"
                  ? "bg-violet-600/20 text-violet-100"
                  : ""
              }`}
                style={msg.role === "assistant" ? {
                  background: "var(--card)",
                  border: "1px solid var(--border)",
                } : {}}>
                <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                <div className="text-[10px] mt-1 opacity-40">
                  {new Date(msg.timestamp).toLocaleTimeString()}
                </div>
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex justify-start">
              <div className="rounded-lg px-3 py-2 text-sm"
                style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-2" style={{ color: "var(--muted)" }}>
                  <div className="animate-pulse">Thinking...</div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Input */}
        <div className="px-4 py-3" style={{ borderTop: "1px solid var(--border)" }}>
          <div className="flex gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about your trading..."
              className="flex-1 px-3 py-2 text-sm rounded-lg border bg-transparent resize-none"
              style={{ borderColor: "var(--border)", background: "var(--card)", minHeight: "40px", maxHeight: "100px" }}
              rows={1}
              disabled={loading}
            />
            <button
              onClick={sendMessage}
              disabled={!input.trim() || loading}
              className="px-3 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-40 transition-colors">
              <Send size={14} className="text-white" />
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
