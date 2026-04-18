"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import api from "@/lib/api";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import { Eye, EyeOff } from "lucide-react";

export default function RegisterPage() {
  const [inviteCode, setInviteCode] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [dateOfBirth, setDateOfBirth] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const getPasswordStrength = (pw: string): number => {
    if (pw.length === 0) return 0;
    let score = 0;
    if (/[a-z]/.test(pw)) score++;
    if (/[A-Z]/.test(pw)) score++;
    if (/[0-9]/.test(pw)) score++;
    if (/[^a-zA-Z0-9]/.test(pw)) score++;
    if (pw.length < 8) return 1; // Always weak if under 8 chars
    if (score <= 1) return 1;
    if (score <= 3) return 2;
    return 3;
  };
  const passwordStrength = getPasswordStrength(password);
  const strengthLabels = ["", "Weak", "Good", "Strong"];
  const strengthColors = ["", "bg-red-500", "bg-amber-500", "bg-emerald-500"];

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inviteCode.trim()) {
      toast.error("Invite code is required");
      return;
    }
    if (password !== confirm) {
      toast.error("Passwords do not match");
      return;
    }
    if (password.length < 12) {
      toast.error("Password must be at least 12 characters with uppercase, lowercase, and a digit");
      return;
    }
    if (!termsAccepted) {
      toast.error("You must accept the Terms of Service and Privacy Policy");
      return;
    }
    if (dateOfBirth) {
      const dob = new Date(dateOfBirth);
      const today = new Date();
      let age = today.getFullYear() - dob.getFullYear();
      const m = today.getMonth() - dob.getMonth();
      if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) age--;
      if (age < 18) {
        toast.error("You must be at least 18 years old to use this platform");
        return;
      }
    }
    setLoading(true);
    try {
      const payload: Record<string, unknown> = {
        email,
        password,
        invite_code: inviteCode,
        terms_accepted: termsAccepted,
      };
      if (dateOfBirth) payload.date_of_birth = dateOfBirth;
      const res = await api.post("/api/auth/register", payload);
      localStorage.setItem("access_token", res.data.access_token);
      if (res.data.refresh_token) localStorage.setItem("refresh_token", res.data.refresh_token);
      toast.success("Account created successfully");
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

        <h1 className="text-xl font-semibold mb-1 bg-gradient-to-r from-violet-400 to-blue-400 bg-clip-text text-transparent">Create Account</h1>
        <p className="text-sm mb-6" style={{ color: "var(--muted)" }}>Invite-only platform for ML-powered trading</p>

        <form onSubmit={handleRegister} className="space-y-4">
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Invite Code</label>
            <input type="text" value={inviteCode} onChange={(e) => setInviteCode(e.target.value)} required
              className="w-full px-3 py-2.5 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} placeholder="Enter your invite code" />
          </div>

          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
              className="w-full px-3 py-2.5 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} placeholder="you@example.com" />
          </div>

          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Password</label>
            <div className="relative">
              <input type={showPassword ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} required
                className="w-full px-3 py-2.5 pr-10 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                style={{ borderColor: "var(--border)" }} placeholder="Min 8 characters" />
              <button type="button" onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 p-0.5" style={{ color: "var(--muted)" }}>
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            {password.length > 0 && (
              <div className="flex items-center gap-2 mt-1.5">
                <div className="flex gap-1 flex-1">
                  {[1, 2, 3].map((level) => (
                    <div key={level} className={`h-1 flex-1 rounded-full ${passwordStrength >= level ? strengthColors[passwordStrength] : "bg-zinc-700"}`} />
                  ))}
                </div>
                <span className="text-xs" style={{ color: "var(--muted)" }}>{strengthLabels[passwordStrength]}</span>
              </div>
            )}
          </div>

          <div>
            <label htmlFor="reg-confirm" className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Confirm Password</label>
            <input id="reg-confirm" type={showPassword ? "text" : "password"} value={confirm} onChange={(e) => setConfirm(e.target.value)} required
              className="w-full px-3 py-2.5 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} placeholder="Re-enter password" />
          </div>

          <div>
            <label htmlFor="reg-dob" className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Date of Birth</label>
            <input id="reg-dob" type="date" value={dateOfBirth} onChange={(e) => setDateOfBirth(e.target.value)}
              className="w-full px-3 py-2.5 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} />
            <p className="text-[10px] mt-1" style={{ color: "var(--muted)" }}>You must be 18+ to use this platform (financial trading requirement)</p>
          </div>

          <label className="flex items-start gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={termsAccepted} onChange={(e) => setTermsAccepted(e.target.checked)}
              className="rounded mt-0.5" required />
            <span style={{ color: "var(--muted)" }}>
              I accept the <a href="/terms" target="_blank" className="text-blue-400 hover:underline">Terms of Service</a> and <a href="/privacy" target="_blank" className="text-blue-400 hover:underline">Privacy Policy</a>.
              I understand this platform trades with real or simulated money.
            </span>
          </label>

          <button type="submit" disabled={loading || !termsAccepted}
            className="w-full py-2.5 text-sm font-medium rounded-lg btn-gradient text-white disabled:opacity-50">
            {loading ? "Creating..." : "Create Account"}
          </button>
        </form>

        <p className="text-xs text-center mt-4" style={{ color: "var(--muted)" }}>
          Already have an account?{" "}
          <Link href="/login" className="text-blue-400 hover:text-blue-300">Sign in</Link>
        </p>
        <p className="text-xs text-center mt-2" style={{ color: "var(--muted)" }}>
          Don&apos;t have an invite code?{" "}
          <Link href="/" className="text-blue-400 hover:text-blue-300">Request access</Link>
        </p>
      </div>
    </div>
  );
}
