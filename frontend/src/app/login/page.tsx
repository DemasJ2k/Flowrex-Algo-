"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import api from "@/lib/api";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  // Forgot password state
  const [showForgot, setShowForgot] = useState(false);
  const [forgotEmail, setForgotEmail] = useState("");
  const [forgotLoading, setForgotLoading] = useState(false);
  const [resetToken, setResetToken] = useState("");
  const [showResetForm, setShowResetForm] = useState(false);
  const [resetTokenInput, setResetTokenInput] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [resetLoading, setResetLoading] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await api.post("/api/auth/login", { email, password });
      const { access_token, refresh_token, token_type } = res.data;

      if (token_type === "2fa_required") {
        localStorage.setItem("2fa_token", access_token);
        toast.info("2FA verification required");
        router.push("/2fa");
        return;
      }

      localStorage.setItem("access_token", access_token);
      if (refresh_token) localStorage.setItem("refresh_token", refresh_token);
      toast.success("Logged in successfully");
      router.push("/");
    } catch (e) {
      toast.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const handleForgotPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setForgotLoading(true);
    try {
      const res = await api.post("/api/auth/forgot-password", { email: forgotEmail });
      setResetToken(res.data.reset_token || "");
      setResetTokenInput(res.data.reset_token || "");
      setShowResetForm(true);
      toast.success("Reset token generated. Use it below to set a new password.");
    } catch (e) {
      toast.error(getErrorMessage(e));
    } finally {
      setForgotLoading(false);
    }
  };

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword.length < 8) {
      toast.error("Password must be at least 8 characters");
      return;
    }
    setResetLoading(true);
    try {
      await api.post("/api/auth/reset-password", {
        token: resetTokenInput,
        new_password: newPassword,
      });
      toast.success("Password reset successfully. You can now sign in.");
      setShowForgot(false);
      setShowResetForm(false);
      setResetToken("");
      setResetTokenInput("");
      setNewPassword("");
      setForgotEmail("");
    } catch (e) {
      toast.error(getErrorMessage(e));
    } finally {
      setResetLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 relative auth-glow" style={{ background: "var(--background)" }}>
      <div className="w-full max-w-sm rounded-xl border p-8 relative z-10 animate-fade-in" style={{ background: "var(--card)", borderColor: "var(--border)" }}>
        <div className="flex items-center gap-2 mb-6">
          <img src="/logo-icon.png" alt="FlowrexAlgo" className="w-8 h-8 rounded-lg object-contain" />
          <span className="text-lg font-semibold">FlowrexAlgo</span>
        </div>

        <h1 className="text-xl font-semibold mb-1 bg-gradient-to-r from-violet-400 to-blue-400 bg-clip-text text-transparent">Sign In</h1>
        <p className="text-sm mb-6" style={{ color: "var(--muted)" }}>Enter your credentials to continue</p>

        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
              className="w-full px-3 py-2.5 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} placeholder="you@example.com" />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
              className="w-full px-3 py-2.5 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} placeholder="••••••••" />
          </div>
          <div className="flex justify-end">
            <button type="button" onClick={() => setShowForgot(!showForgot)}
              className="text-xs text-blue-400 hover:text-blue-300">
              Forgot password?
            </button>
          </div>
          <button type="submit" disabled={loading}
            className="w-full py-2.5 text-sm font-medium rounded-lg btn-gradient text-white disabled:opacity-50">
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>

        {/* Forgot Password Inline Form */}
        {showForgot && (
          <div className="mt-4 pt-4 border-t" style={{ borderColor: "var(--border)" }}>
            {!showResetForm ? (
              <form onSubmit={handleForgotPassword} className="space-y-3">
                <p className="text-xs font-medium" style={{ color: "var(--muted)" }}>
                  Enter your email to receive a reset token
                </p>
                <input type="email" value={forgotEmail} onChange={(e) => setForgotEmail(e.target.value)} required
                  className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                  style={{ borderColor: "var(--border)" }} placeholder="you@example.com" />
                <button type="submit" disabled={forgotLoading}
                  className="w-full py-2 text-xs font-medium rounded-lg btn-gradient text-white disabled:opacity-50">
                  {forgotLoading ? "Sending..." : "Send Reset Token"}
                </button>
              </form>
            ) : (
              <form onSubmit={handleResetPassword} className="space-y-3">
                <p className="text-xs font-medium text-green-400">
                  Reset token generated. Enter it below with your new password.
                </p>
                {resetToken && (
                  <div className="p-2 rounded-lg text-xs font-mono break-all" style={{ background: "var(--background)", color: "var(--muted)" }}>
                    Token: {resetToken}
                  </div>
                )}
                <div>
                  <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Reset Token</label>
                  <input type="text" value={resetTokenInput} onChange={(e) => setResetTokenInput(e.target.value)} required
                    className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                    style={{ borderColor: "var(--border)" }} placeholder="Paste reset token" />
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>New Password</label>
                  <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required
                    className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                    style={{ borderColor: "var(--border)" }} placeholder="Min 8 characters" />
                </div>
                <button type="submit" disabled={resetLoading}
                  className="w-full py-2 text-xs font-medium rounded-lg btn-gradient text-white disabled:opacity-50">
                  {resetLoading ? "Resetting..." : "Reset Password"}
                </button>
                <button type="button" onClick={() => { setShowResetForm(false); setResetToken(""); }}
                  className="w-full py-2 text-xs text-center" style={{ color: "var(--muted)" }}>
                  Back
                </button>
              </form>
            )}
          </div>
        )}

        <p className="text-xs text-center mt-4" style={{ color: "var(--muted)" }}>
          Don&apos;t have an account?{" "}
          <Link href="/register" className="text-blue-400 hover:text-blue-300">Create one</Link>
        </p>
      </div>
    </div>
  );
}
