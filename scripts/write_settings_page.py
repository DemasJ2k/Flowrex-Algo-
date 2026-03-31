"""Write the updated Settings page."""
import os
TARGET = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "app", "settings", "page.tsx")

CONTENT = '''"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import Card from "@/components/ui/Card";
import StatusBadge from "@/components/ui/StatusBadge";
import Tabs from "@/components/ui/Tabs";
import BrokerModal from "@/components/BrokerModal";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import type { UserSettings } from "@/types";
import { Download, Shield, ShieldCheck, Key, Eye, EyeOff, Plug, Trash2, Loader2 } from "lucide-react";

interface UserProfile { id: number; email: string; is_admin: boolean; created_at: string | null; has_2fa: boolean; }
interface BrokerConnection { broker_name: string; stored: boolean; is_active: boolean; connected: boolean; balance: number | null; currency: string | null; }

export default function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings>({ theme: "dark", default_broker: null, notifications_enabled: true, settings_json: {} });
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [connections, setConnections] = useState<BrokerConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [brokerModal, setBrokerModal] = useState(false);

  // Password form
  const [curPass, setCurPass] = useState("");
  const [newPass, setNewPass] = useState("");
  const [confirmPass, setConfirmPass] = useState("");
  const [showPass, setShowPass] = useState(false);

  // 2FA
  const [tfaSetup, setTfaSetup] = useState<{secret: string; provisioning_uri: string} | null>(null);
  const [tfaCode, setTfaCode] = useState("");

  // Data
  const [clearLogsConfirm, setClearLogsConfirm] = useState(false);

  const fetchData = () => {
    Promise.all([
      api.get("/api/settings/").then((r) => setSettings(r.data)).catch(() => {}),
      api.get("/api/auth/me").then((r) => setProfile(r.data)).catch(() => {}),
      api.get("/api/broker/connections").then((r) => setConnections(r.data)).catch(() => {}),
    ]).finally(() => setLoading(false));
  };
  useEffect(() => { fetchData(); }, []);

  const fmt = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const handleSaveSettings = async () => {
    try {
      await api.put("/api/settings/", settings);
      toast.success("Settings saved");
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleChangePassword = async () => {
    if (newPass !== confirmPass) { toast.error("Passwords do not match"); return; }
    if (newPass.length < 8) { toast.error("Password must be at least 8 characters"); return; }
    try {
      await api.put("/api/auth/change-password", null, { params: { current_password: curPass, new_password: newPass } });
      toast.success("Password updated");
      setCurPass(""); setNewPass(""); setConfirmPass("");
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleSetup2FA = async () => {
    try {
      const res = await api.post("/api/auth/2fa/setup");
      setTfaSetup(res.data);
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleVerify2FA = async () => {
    try {
      await api.post("/api/auth/2fa/verify", null, { params: { code: tfaCode } });
      toast.success("2FA enabled!");
      setTfaSetup(null); setTfaCode("");
      fetchData();
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleDisable2FA = async () => {
    try {
      await api.post("/api/auth/2fa/disable");
      toast.success("2FA disabled");
      fetchData();
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleDisconnect = async (broker: string) => {
    try {
      await api.post("/api/broker/disconnect", { broker_name: broker });
      toast.success(broker + " disconnected");
      fetchData();
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const downloadCSV = async (endpoint: string, filename: string) => {
    try {
      const res = await api.get(endpoint);
      const data = res.data;
      if (!Array.isArray(data) || data.length === 0) { toast.error("No data to export"); return; }
      const headers = Object.keys(data[0]);
      const csv = [headers.join(","), ...data.map((row: Record<string, unknown>) => headers.map((h) => JSON.stringify(row[h] ?? "")).join(","))].join("\\n");
      const blob = new Blob([csv], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
      toast.success("Downloaded " + filename);
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  if (loading) return <div className="flex items-center justify-center h-64"><Loader2 size={24} className="animate-spin" style={{ color: "var(--muted)" }} /></div>;

  return (
    <div className="space-y-4 max-w-3xl">
      <h1 className="text-2xl font-semibold">Settings</h1>

      <Tabs tabs={[
        {
          label: "Account",
          content: (
            <div className="space-y-4">
              {/* Profile */}
              <Card>
                <h3 className="text-sm font-medium mb-3">Profile</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between"><span style={{ color: "var(--muted)" }}>Email</span><span>{profile?.email || "\\u2014"}</span></div>
                  <div className="flex justify-between"><span style={{ color: "var(--muted)" }}>Role</span><span>{profile?.is_admin ? "Admin" : "User"}</span></div>
                  <div className="flex justify-between"><span style={{ color: "var(--muted)" }}>Member since</span><span>{profile?.created_at ? new Date(profile.created_at).toLocaleDateString() : "\\u2014"}</span></div>
                  <div className="flex justify-between"><span style={{ color: "var(--muted)" }}>2FA</span><span>{profile?.has_2fa ? "Enabled" : "Not enabled"}</span></div>
                </div>
              </Card>

              {/* Change Password */}
              <Card>
                <h3 className="text-sm font-medium mb-3">Change Password</h3>
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Current Password</label>
                    <div className="relative">
                      <input type={showPass ? "text" : "password"} value={curPass} onChange={(e) => setCurPass(e.target.value)}
                        className="w-full px-3 py-2 pr-10 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500" style={{ borderColor: "var(--border)" }} />
                      <button type="button" onClick={() => setShowPass(!showPass)} className="absolute right-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted)" }}>
                        {showPass ? <EyeOff size={14} /> : <Eye size={14} />}
                      </button>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>New Password</label>
                      <input type={showPass ? "text" : "password"} value={newPass} onChange={(e) => setNewPass(e.target.value)}
                        className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500" style={{ borderColor: "var(--border)" }} placeholder="Min 8 chars" />
                    </div>
                    <div>
                      <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Confirm</label>
                      <input type={showPass ? "text" : "password"} value={confirmPass} onChange={(e) => setConfirmPass(e.target.value)}
                        className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500" style={{ borderColor: "var(--border)" }} />
                    </div>
                  </div>
                  <button onClick={handleChangePassword} disabled={!curPass || !newPass}
                    className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">Update Password</button>
                </div>
              </Card>

              {/* Preferences */}
              <Card>
                <h3 className="text-sm font-medium mb-3">Preferences</h3>
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Theme</label>
                    <select value={settings.theme} onChange={(e) => setSettings({ ...settings, theme: e.target.value })}
                      className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                      <option value="dark">Dark</option><option value="light">Light</option>
                    </select>
                  </div>
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" checked={settings.notifications_enabled} onChange={(e) => setSettings({ ...settings, notifications_enabled: e.target.checked })} className="rounded" />
                    Enable notifications
                  </label>
                  <button onClick={handleSaveSettings} className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white">Save Preferences</button>
                </div>
              </Card>
            </div>
          ),
        },
        {
          label: "Trading",
          content: (
            <div className="space-y-4">
              {/* Default Broker */}
              <Card>
                <h3 className="text-sm font-medium mb-3">Default Trading Config</h3>
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Default Broker</label>
                    <select value={settings.default_broker || ""} onChange={(e) => setSettings({ ...settings, default_broker: e.target.value || null })}
                      className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                      <option value="">None</option><option value="oanda">Oanda</option><option value="ctrader">cTrader</option><option value="mt5">MT5</option>
                    </select>
                  </div>
                  <button onClick={handleSaveSettings} className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white">Save</button>
                </div>
              </Card>

              {/* Broker Connections */}
              <Card>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-medium">Broker Connections</h3>
                  <button onClick={() => setBrokerModal(true)} className="text-xs text-blue-400 hover:text-blue-300">+ Add Connection</button>
                </div>
                <div className="space-y-2">
                  {connections.map((c) => (
                    <div key={c.broker_name} className="flex items-center justify-between px-3 py-2.5 rounded-lg border" style={{ borderColor: "var(--border)" }}>
                      <div className="flex items-center gap-3">
                        <span className="text-sm font-medium uppercase">{c.broker_name}</span>
                        {c.connected ? (
                          <span className="flex items-center gap-1 text-xs text-emerald-400"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />Connected</span>
                        ) : (
                          <span className="text-xs" style={{ color: "var(--muted)" }}>Not connected</span>
                        )}
                        {c.balance !== null && <span className="text-xs" style={{ color: "var(--muted)" }}>{fmt(c.balance)} {c.currency}</span>}
                      </div>
                      {c.connected ? (
                        <button onClick={() => handleDisconnect(c.broker_name)} className="text-xs px-2 py-1 rounded border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>Disconnect</button>
                      ) : (
                        <button onClick={() => setBrokerModal(true)} className="text-xs px-2 py-1 rounded border hover:bg-white/5 text-blue-400" style={{ borderColor: "var(--border)" }}>Connect</button>
                      )}
                    </div>
                  ))}
                </div>
              </Card>
            </div>
          ),
        },
        {
          label: "Security",
          content: (
            <div className="space-y-4">
              <Card>
                <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
                  {profile?.has_2fa ? <ShieldCheck size={16} className="text-emerald-400" /> : <Shield size={16} style={{ color: "var(--muted)" }} />}
                  Two-Factor Authentication
                </h3>

                {profile?.has_2fa && !tfaSetup ? (
                  <div>
                    <div className="flex items-center gap-2 mb-3 text-sm text-emerald-400">
                      <ShieldCheck size={16} /> 2FA is enabled
                    </div>
                    <button onClick={handleDisable2FA} className="px-3 py-1.5 text-xs rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10">Disable 2FA</button>
                  </div>
                ) : tfaSetup ? (
                  <div className="space-y-3">
                    <p className="text-sm" style={{ color: "var(--muted)" }}>Scan this QR code with your authenticator app (Google Authenticator, Authy, etc.)</p>
                    <div className="p-4 bg-white rounded-lg inline-block">
                      <img src={"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=" + encodeURIComponent(tfaSetup.provisioning_uri)} alt="2FA QR Code" width={200} height={200} />
                    </div>
                    <div className="text-xs" style={{ color: "var(--muted)" }}>
                      Manual key: <code className="px-1 py-0.5 rounded bg-white/10">{tfaSetup.secret}</code>
                    </div>
                    <div className="flex gap-2">
                      <input value={tfaCode} onChange={(e) => setTfaCode(e.target.value)} placeholder="Enter 6-digit code"
                        className="px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500 w-40" style={{ borderColor: "var(--border)" }} />
                      <button onClick={handleVerify2FA} disabled={tfaCode.length < 6}
                        className="px-4 py-2 text-sm font-medium rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50">Verify</button>
                      <button onClick={() => setTfaSetup(null)} className="px-3 py-2 text-sm rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>Cancel</button>
                    </div>
                  </div>
                ) : (
                  <div>
                    <p className="text-sm mb-3" style={{ color: "var(--muted)" }}>Add an extra layer of security to your account</p>
                    <button onClick={handleSetup2FA} className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white">
                      <Key size={14} /> Enable 2FA
                    </button>
                  </div>
                )}
              </Card>
            </div>
          ),
        },
        {
          label: "Data",
          content: (
            <div className="space-y-4">
              <Card>
                <h3 className="text-sm font-medium mb-3">Export Data</h3>
                <div className="space-y-3">
                  <div className="flex items-center justify-between py-2">
                    <div>
                      <p className="text-sm">Trade History</p>
                      <p className="text-xs" style={{ color: "var(--muted)" }}>Export all trades as CSV</p>
                    </div>
                    <button onClick={() => downloadCSV("/api/agents/all-trades?limit=10000", "trades.csv")}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>
                      <Download size={14} /> Download
                    </button>
                  </div>
                  <div className="flex items-center justify-between py-2 border-t" style={{ borderColor: "var(--border)" }}>
                    <div>
                      <p className="text-sm">Engine Logs</p>
                      <p className="text-xs" style={{ color: "var(--muted)" }}>Export agent logs as CSV</p>
                    </div>
                    <button onClick={() => downloadCSV("/api/agents/engine-logs?limit=10000", "engine_logs.csv")}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>
                      <Download size={14} /> Download
                    </button>
                  </div>
                </div>
              </Card>

              <Card>
                <h3 className="text-sm font-medium mb-3 text-red-400">Danger Zone</h3>
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm">Clear all agent logs</p>
                    <p className="text-xs" style={{ color: "var(--muted)" }}>This does not delete trades or agents</p>
                  </div>
                  <button onClick={() => setClearLogsConfirm(true)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10">
                    <Trash2 size={14} /> Clear Logs
                  </button>
                </div>
              </Card>

              <ConfirmDialog open={clearLogsConfirm} onClose={() => setClearLogsConfirm(false)}
                onConfirm={() => { toast.info("Log clearing not yet implemented"); setClearLogsConfirm(false); }}
                title="Clear Agent Logs" message="Delete all engine logs? This cannot be undone. Trades and agents are not affected."
                confirmLabel="Clear All Logs" variant="danger" />
            </div>
          ),
        },
      ]} />

      <BrokerModal open={brokerModal} onClose={() => setBrokerModal(false)} onConnected={fetchData} />
    </div>
  );
}
'''

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(CONTENT)
print(f"Written {len(CONTENT)} chars")
