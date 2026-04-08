"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";

export default function TwoFactorPage() {
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // If no 2fa_token, redirect to login
    if (typeof window !== "undefined") {
      const token = localStorage.getItem("2fa_token");
      if (!token) {
        router.push("/login");
      }
    }
    // Auto-focus the input
    inputRef.current?.focus();
  }, [router]);

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (code.length !== 6) {
      toast.error("Please enter a 6-digit code");
      return;
    }
    setLoading(true);
    try {
      const twoFaToken = localStorage.getItem("2fa_token");
      if (!twoFaToken) {
        toast.error("Session expired. Please log in again.");
        router.push("/login");
        return;
      }

      // Send the 2fa_token as Authorization header for this request
      const res = await api.post(
        `/api/auth/2fa/verify?code=${encodeURIComponent(code)}`,
        {},
        {
          headers: {
            Authorization: `Bearer ${twoFaToken}`,
          },
        }
      );

      const { access_token, refresh_token } = res.data;

      // Clear the temporary 2fa token
      localStorage.removeItem("2fa_token");

      // Store the real tokens
      localStorage.setItem("access_token", access_token);
      if (refresh_token) localStorage.setItem("refresh_token", refresh_token);

      toast.success("2FA verified successfully");
      router.push("/");
    } catch (e) {
      toast.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const handleCodeChange = (value: string) => {
    // Only allow digits, max 6
    const cleaned = value.replace(/\D/g, "").slice(0, 6);
    setCode(cleaned);
  };

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4 relative auth-glow"
      style={{ background: "var(--background)" }}
    >
      <div
        className="w-full max-w-sm rounded-xl border p-8 relative z-10 animate-fade-in"
        style={{ background: "var(--card)", borderColor: "var(--border)" }}
      >
        <div className="flex items-center gap-2 mb-6">
          <img
            src="/logo-icon.png"
            alt="FlowrexAlgo"
            className="w-8 h-8 rounded-lg object-contain"
          />
          <span className="text-lg font-semibold">FlowrexAlgo</span>
        </div>

        <h1 className="text-xl font-semibold mb-1 bg-gradient-to-r from-violet-400 to-blue-400 bg-clip-text text-transparent">
          Two-Factor Authentication
        </h1>
        <p className="text-sm mb-6" style={{ color: "var(--muted)" }}>
          Enter the 6-digit code from your authenticator app
        </p>

        <form onSubmit={handleVerify} className="space-y-4">
          <div>
            <label
              className="block text-xs font-medium mb-1"
              style={{ color: "var(--muted)" }}
            >
              Verification Code
            </label>
            <input
              ref={inputRef}
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => handleCodeChange(e.target.value)}
              required
              maxLength={6}
              className="w-full px-3 py-2.5 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500 text-center tracking-[0.5em] text-lg font-mono"
              style={{ borderColor: "var(--border)" }}
              placeholder="000000"
            />
          </div>
          <button
            type="submit"
            disabled={loading || code.length !== 6}
            className="w-full py-2.5 text-sm font-medium rounded-lg btn-gradient text-white disabled:opacity-50"
          >
            {loading ? "Verifying..." : "Verify Code"}
          </button>
        </form>

        <p
          className="text-xs text-center mt-4"
          style={{ color: "var(--muted)" }}
        >
          Lost access to your authenticator?{" "}
          <button
            onClick={() => {
              localStorage.removeItem("2fa_token");
              router.push("/login");
            }}
            className="text-blue-400 hover:text-blue-300"
          >
            Back to login
          </button>
        </p>
      </div>
    </div>
  );
}
