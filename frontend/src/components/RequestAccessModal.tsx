"use client";

import { useState } from "react";
import { X, CheckCircle, Loader2 } from "lucide-react";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function RequestAccessModal({ open, onClose }: Props) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [error, setError] = useState("");

  if (!open) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !email.trim()) return;

    setStatus("loading");
    try {
      const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${API_URL}/api/access-requests`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), email: email.trim(), phone: phone.trim() || null, message: message.trim() || null }),
      });
      if (!res.ok) throw new Error("Failed to submit");
      setStatus("success");
    } catch {
      setStatus("error");
      setError("Failed to submit request. Please try again.");
    }
  };

  const handleClose = () => {
    setStatus("idle");
    setName("");
    setEmail("");
    setPhone("");
    setMessage("");
    setError("");
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }}>
      <div className="w-full max-w-md rounded-xl border p-6 relative" style={{ background: "var(--card)", borderColor: "var(--border)" }}>
        <button onClick={handleClose} className="absolute top-4 right-4 p-1 rounded hover:bg-white/10" style={{ color: "var(--muted)" }}>
          <X size={18} />
        </button>

        {status === "success" ? (
          <div className="text-center py-8">
            <CheckCircle size={48} className="mx-auto mb-4 text-emerald-400" />
            <h3 className="text-lg font-semibold mb-2">Request Submitted!</h3>
            <p className="text-sm" style={{ color: "var(--muted)" }}>
              We&apos;ll review your request and send you an invite code via email.
            </p>
            <button onClick={handleClose} className="mt-6 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white">
              Close
            </button>
          </div>
        ) : (
          <>
            <h3 className="text-lg font-semibold mb-1">Request Access</h3>
            <p className="text-sm mb-6" style={{ color: "var(--muted)" }}>
              Fill out the form below and we&apos;ll send you an invite code.
            </p>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-xs mb-1 font-medium" style={{ color: "var(--muted)" }}>Name *</label>
                <input
                  type="text" value={name} onChange={(e) => setName(e.target.value)} required
                  className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                  style={{ borderColor: "var(--border)" }}
                  placeholder="Your name"
                />
              </div>
              <div>
                <label className="block text-xs mb-1 font-medium" style={{ color: "var(--muted)" }}>Email *</label>
                <input
                  type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
                  className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                  style={{ borderColor: "var(--border)" }}
                  placeholder="you@example.com"
                />
              </div>
              <div>
                <label className="block text-xs mb-1 font-medium" style={{ color: "var(--muted)" }}>Phone (optional)</label>
                <input
                  type="tel" value={phone} onChange={(e) => setPhone(e.target.value)}
                  className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                  style={{ borderColor: "var(--border)" }}
                  placeholder="+1 (555) 000-0000"
                />
              </div>
              <div>
                <label className="block text-xs mb-1 font-medium" style={{ color: "var(--muted)" }}>Why do you want access?</label>
                <textarea
                  value={message} onChange={(e) => setMessage(e.target.value)} rows={3}
                  className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500 resize-none"
                  style={{ borderColor: "var(--border)" }}
                  placeholder="Tell us about your trading experience..."
                />
              </div>

              {error && <p className="text-xs text-red-400">{error}</p>}

              <button
                type="submit"
                disabled={status === "loading" || !name.trim() || !email.trim()}
                className="w-full px-4 py-2.5 text-sm font-semibold rounded-lg text-white disabled:opacity-50 transition-all hover:scale-[1.02] flex items-center justify-center gap-2"
                style={{ background: "linear-gradient(135deg, #8b5cf6, #3b82f6)" }}
              >
                {status === "loading" ? <><Loader2 size={16} className="animate-spin" /> Submitting...</> : "Submit Request"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
