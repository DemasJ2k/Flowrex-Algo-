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

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await api.post("/api/auth/login", { email, password });
      const { access_token, refresh_token, token_type } = res.data;

      if (token_type === "2fa_required") {
        localStorage.setItem("2fa_token", access_token);
        toast.info("2FA verification required");
        // TODO: redirect to 2FA verification page
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
          <button type="submit" disabled={loading}
            className="w-full py-2.5 text-sm font-medium rounded-lg btn-gradient text-white disabled:opacity-50">
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>

        <p className="text-xs text-center mt-4" style={{ color: "var(--muted)" }}>
          Don&apos;t have an account?{" "}
          <Link href="/register" className="text-blue-400 hover:text-blue-300">Create one</Link>
        </p>
      </div>
    </div>
  );
}
