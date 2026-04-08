"use client";

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
import { Download, Shield, ShieldCheck, Key, Eye, EyeOff, Trash2, Loader2, Database, Plus, CheckCircle, XCircle, MessageSquare } from "lucide-react";

interface UserProfile { id: number; email: string; is_admin: boolean; created_at: string | null; has_2fa: boolean; }
interface BrokerConnection { broker_name: string; stored: boolean; is_active: boolean; connected: boolean; balance: number | null; currency: string | null; account_id: string | null; server: string | null; connected_since: number | null; }

interface TradingDefaults { risk_per_trade: number; max_daily_loss_pct: number; max_positions: number; cooldown_bars: number; }
interface ApiKeys { finnhub: string; alphavantage: string; newsapi: string; }
interface ModelFeatureToggles { use_correlations: boolean; use_m15_features: boolean; use_external_macro: boolean; }

const DEFAULT_TRADING: TradingDefaults = { risk_per_trade: 0.01, max_daily_loss_pct: 0.03, max_positions: 4, cooldown_bars: 3 };
const DEFAULT_FEATURES: ModelFeatureToggles = { use_correlations: true, use_m15_features: true, use_external_macro: false };
const DEFAULT_KEYS: ApiKeys = { finnhub: "", alphavantage: "", newsapi: "" };

export default function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings>({ theme: "dark", default_broker: null, notifications_enabled: true, settings_json: {} });
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [connections, setConnections] = useState<BrokerConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [brokerModal, setBrokerModal] = useState(false);

  // Trading defaults (derived from settings_json)
  const [trading, setTrading] = useState<TradingDefaults>(DEFAULT_TRADING);
  const [apiKeys, setApiKeys] = useState<ApiKeys>(DEFAULT_KEYS);
  const [modelFeatures, setModelFeatures] = useState<ModelFeatureToggles>(DEFAULT_FEATURES);
  const [showKey, setShowKey] = useState<Record<string, boolean>>({});

  // Password form
  const [curPass, setCurPass] = useState("");
  const [newPass, setNewPass] = useState("");
  const [confirmPass, setConfirmPass] = useState("");
  const [showPass, setShowPass] = useState(false);

  // 2FA
  const [tfaSetup, setTfaSetup] = useState<{secret: string; provisioning_uri: string} | null>(null);
  const [tfaCode, setTfaCode] = useState("");

  // Data Providers
  interface DataProvider { id: number; provider_name: string; api_key_masked: string; data_type: string; is_active: boolean; }
  const [providers, setProviders] = useState<DataProvider[]>([]);
  const [newProvider, setNewProvider] = useState({ name: "databento", key: "", type: "ohlcv" });
  const [providerTesting, setProviderTesting] = useState<number | null>(null);

  // Feedback
  const [feedbackType, setFeedbackType] = useState("bug");
  const [feedbackMsg, setFeedbackMsg] = useState("");
  const [feedbackSending, setFeedbackSending] = useState(false);

  // Data
  const [clearLogsConfirm, setClearLogsConfirm] = useState(false);

  const fetchData = () => {
    Promise.all([
      api.get("/api/settings/").then((r) => {
        const s = r.data;
        setSettings(s);
        // Populate trading defaults, API keys, and model feature toggles from settings_json
        if (s.settings_json?.trading) setTrading({ ...DEFAULT_TRADING, ...s.settings_json.trading });
        if (s.settings_json?.api_keys) setApiKeys({ ...DEFAULT_KEYS, ...s.settings_json.api_keys });
        if (s.settings_json?.model_features) setModelFeatures({ ...DEFAULT_FEATURES, ...s.settings_json.model_features });
      }).catch(() => {}),
      api.get("/api/auth/me").then((r) => setProfile(r.data)).catch(() => {}),
      api.get("/api/broker/connections").then((r) => setConnections(r.data)).catch(() => {}),
      api.get("/api/market-data/providers").then((r) => setProviders(r.data)).catch(() => {}),
    ]).finally(() => setLoading(false));
  };
  useEffect(() => { fetchData(); }, []);

  // Apply theme when settings load or change
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", settings.theme);
    if (typeof window !== "undefined") localStorage.setItem("flowrex_theme", settings.theme);
  }, [settings.theme]);

  // Recycle bin
  const [recycleBin, setRecycleBin] = useState<Array<{id: number; name: string; symbol: string; deleted_at: string}>>([]);
  useEffect(() => {
    api.get("/api/agents/recycle-bin").then((r) => setRecycleBin(r.data)).catch(() => {});
  }, []);

  const handleRestore = async (id: number) => {
    try {
      const res = await api.post("/api/agents/recycle-bin/" + id + "/restore");
      toast.success("Restored: " + res.data.name);
      api.get("/api/agents/recycle-bin").then((r) => setRecycleBin(r.data)).catch(() => {});
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handlePurge = async (id: number) => {
    try {
      await api.delete("/api/agents/recycle-bin/" + id + "/purge");
      toast.success("Permanently deleted");
      api.get("/api/agents/recycle-bin").then((r) => setRecycleBin(r.data)).catch(() => {});
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handlePurgeAll = async () => {
    try {
      const res = await api.delete("/api/agents/recycle-bin/purge-all");
      toast.success(res.data.message);
      setRecycleBin([]);
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const fmt = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // Live uptime counter
  const [uptimeTick, setUptimeTick] = useState(0);
  useEffect(() => {
    const hasConnected = connections.some((c) => c.connected && c.connected_since);
    if (!hasConnected) return;
    const iv = setInterval(() => setUptimeTick((t) => t + 1), 1000);
    return () => clearInterval(iv);
  }, [connections]);

  const fmtUptime = (since: number | null) => {
    if (!since) return "";
    void uptimeTick; // triggers re-render each second
    const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - since));
    if (elapsed < 60) return `${elapsed}s`;
    if (elapsed < 3600) return `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
    const h = Math.floor(elapsed / 3600);
    const m = Math.floor((elapsed % 3600) / 60);
    return `${h}h ${m}m`;
  };

  const handleSaveSettings = async () => {
    try {
      await api.put("/api/settings/", settings);
      document.documentElement.setAttribute("data-theme", settings.theme);
      toast.success("Settings saved");
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleSaveTradingDefaults = async () => {
    try {
      const merged_json = { ...(settings.settings_json || {}), trading };
      await api.put("/api/settings/", { settings_json: merged_json });
      setSettings((s) => ({ ...s, settings_json: merged_json }));
      toast.success("Trading defaults saved");
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleSaveModelFeatures = async (updated?: ModelFeatureToggles) => {
    const toSave = updated ?? modelFeatures;
    try {
      const merged_json = { ...(settings.settings_json || {}), model_features: toSave };
      await api.put("/api/settings/", { settings_json: merged_json });
      setSettings((s) => ({ ...s, settings_json: merged_json }));
      toast.success("Model feature settings saved");
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleSaveApiKeys = async () => {
    try {
      const merged_json = { ...(settings.settings_json || {}), api_keys: apiKeys };
      await api.put("/api/settings/", { settings_json: merged_json });
      setSettings((s) => ({ ...s, settings_json: merged_json }));
      toast.success("API keys saved");
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleChangePassword = async () => {
    if (newPass !== confirmPass) { toast.error("Passwords do not match"); return; }
    if (newPass.length < 8) { toast.error("Password must be at least 8 characters"); return; }
    try {
      await api.put("/api/auth/change-password", { current_password: curPass, new_password: newPass });
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

  const handleClearLogs = async () => {
    try {
      const res = await api.delete("/api/agents/logs");
      toast.success(res.data.message || "Logs cleared");
      setClearLogsConfirm(false);
    } catch (e: unknown) { toast.error(getErrorMessage(e)); setClearLogsConfirm(false); }
  };

  const downloadJSON = async (endpoint: string, filename: string) => {
    try {
      const res = await api.get(endpoint);
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
      toast.success("Downloaded " + filename);
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const downloadCSV = async (endpoint: string, filename: string) => {
    try {
      const res = await api.get(endpoint);
      const data = res.data;
      if (!Array.isArray(data) || data.length === 0) { toast.error("No data to export"); return; }
      const headers = Object.keys(data[0]);
      const csv = [headers.join(","), ...data.map((row: Record<string, unknown>) => headers.map((h) => JSON.stringify(row[h] ?? "")).join(","))].join("\n");
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
      <h1 className="text-2xl font-semibold bg-gradient-to-r from-violet-400 to-blue-400 bg-clip-text text-transparent">Settings</h1>

      <Tabs tabs={[
        {
          label: "Account",
          content: (
            <div className="space-y-4">
              {/* Profile */}
              <Card>
                <h3 className="text-sm font-medium mb-3">Profile</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between"><span style={{ color: "var(--muted)" }}>Email</span><span>{profile?.email || "\u2014"}</span></div>
                  <div className="flex justify-between"><span style={{ color: "var(--muted)" }}>Role</span><span>{profile?.is_admin ? "Admin" : "User"}</span></div>
                  <div className="flex justify-between"><span style={{ color: "var(--muted)" }}>Member since</span><span>{profile?.created_at ? new Date(profile.created_at).toLocaleDateString() : "\u2014"}</span></div>
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
              {/* Default Trading Config */}
              <Card>
                <h3 className="text-sm font-medium mb-4">Default Trading Configuration</h3>
                <div className="space-y-4">
                  {/* Default Broker */}
                  <div>
                    <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Default Broker</label>
                    <select value={settings.default_broker || ""} onChange={(e) => setSettings({ ...settings, default_broker: e.target.value || null })}
                      className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                      <option value="">None</option><option value="oanda">Oanda</option><option value="ctrader">cTrader</option><option value="mt5">MT5</option>
                    </select>
                  </div>
                  {/* 2-col grid for numeric defaults */}
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Risk per Trade (%)</label>
                      <input type="number" min="0.1" max="3" step="0.1"
                        value={(trading.risk_per_trade * 100).toFixed(1)}
                        onChange={(e) => setTrading({ ...trading, risk_per_trade: parseFloat(e.target.value) / 100 || DEFAULT_TRADING.risk_per_trade })}
                        className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500" style={{ borderColor: "var(--border)" }} />
                    </div>
                    <div>
                      <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Max Daily Loss (%)</label>
                      <input type="number" min="1" max="10" step="0.5"
                        value={(trading.max_daily_loss_pct * 100).toFixed(1)}
                        onChange={(e) => setTrading({ ...trading, max_daily_loss_pct: parseFloat(e.target.value) / 100 || DEFAULT_TRADING.max_daily_loss_pct })}
                        className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500" style={{ borderColor: "var(--border)" }} />
                    </div>
                    <div>
                      <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Max Open Positions</label>
                      <input type="number" min="1" max="10" step="1"
                        value={trading.max_positions}
                        onChange={(e) => setTrading({ ...trading, max_positions: Math.min(10, Math.max(1, parseInt(e.target.value) || DEFAULT_TRADING.max_positions)) })}
                        className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500" style={{ borderColor: "var(--border)" }} />
                    </div>
                    <div>
                      <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Cooldown Bars</label>
                      <input type="number" min="1" max="20" step="1"
                        value={trading.cooldown_bars}
                        onChange={(e) => setTrading({ ...trading, cooldown_bars: Math.min(20, Math.max(1, parseInt(e.target.value) || DEFAULT_TRADING.cooldown_bars)) })}
                        className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500" style={{ borderColor: "var(--border)" }} />
                    </div>
                  </div>
                  <p className="text-xs" style={{ color: "var(--muted)" }}>These are defaults applied when creating a new agent. Each agent can override them individually.</p>
                  <button onClick={() => { handleSaveSettings(); handleSaveTradingDefaults(); }}
                    className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white">
                    Save Trading Defaults
                  </button>
                </div>
              </Card>

              {/* Agent Filters */}
              <Card>
                <h3 className="text-sm font-medium mb-1">Default Agent Filters</h3>
                <p className="text-xs mb-4" style={{ color: "var(--muted)" }}>
                  Filters applied to new agents by default. Each agent can override these individually via Edit Config.
                </p>
                <div className="space-y-3">
                  {/* News Filter */}
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1">
                      <p className="text-sm font-medium">News Filter</p>
                      <p className="text-xs mt-0.5" style={{ color: "var(--muted)" }}>
                        Skip trading during high-impact economic events (NFP, FOMC, CPI).
                        Uses Finnhub API to check for upcoming events. Recommended ON.
                      </p>
                    </div>
                    <button
                      onClick={() => {
                        const updated = { ...modelFeatures, use_correlations: !modelFeatures.use_correlations };
                        setModelFeatures(updated);
                        handleSaveModelFeatures(updated);
                      }}
                      className={`flex-shrink-0 relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${modelFeatures.use_correlations ? "bg-blue-600" : "bg-white/10"}`}
                    >
                      <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${modelFeatures.use_correlations ? "translate-x-6" : "translate-x-1"}`} />
                    </button>
                  </div>

                  {/* Session Filter */}
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1">
                      <p className="text-sm font-medium">Session Filter</p>
                      <p className="text-xs mt-0.5" style={{ color: "var(--muted)" }}>
                        Reduce risk during low-liquidity hours (Asian session for indices).
                        Agents skip signals outside prime trading hours for each symbol.
                      </p>
                    </div>
                    <button
                      onClick={() => {
                        const updated = { ...modelFeatures, use_m15_features: !modelFeatures.use_m15_features };
                        setModelFeatures(updated);
                        handleSaveModelFeatures(updated);
                      }}
                      className={`flex-shrink-0 relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${modelFeatures.use_m15_features ? "bg-blue-600" : "bg-white/10"}`}
                    >
                      <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${modelFeatures.use_m15_features ? "translate-x-6" : "translate-x-1"}`} />
                    </button>
                  </div>

                  {/* External Macro Features */}
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1">
                      <p className="text-sm font-medium">External Macro Features</p>
                      <p className="text-xs mt-0.5" style={{ color: "var(--muted)" }}>
                        VIX fear index, TIPS real yield, 2s10s spread, BTC funding rate and dominance.
                        Requires API keys (Finnhub / AlphaVantage). Keep OFF if keys are not set.
                      </p>
                    </div>
                    <button
                      onClick={() => {
                        const updated = { ...modelFeatures, use_external_macro: !modelFeatures.use_external_macro };
                        setModelFeatures(updated);
                        handleSaveModelFeatures(updated);
                      }}
                      className={`flex-shrink-0 relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${modelFeatures.use_external_macro ? "bg-blue-600" : "bg-white/10"}`}
                    >
                      <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${modelFeatures.use_external_macro ? "translate-x-6" : "translate-x-1"}`} />
                    </button>
                  </div>
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
                    <div key={c.broker_name} className="rounded-lg border overflow-hidden" style={{ borderColor: c.connected ? "var(--border)" : "var(--border)" }}>
                      {/* Header Row */}
                      <div className="flex items-center justify-between px-4 py-3" style={{ background: c.connected ? "rgba(16, 185, 129, 0.05)" : "transparent" }}>
                        <div className="flex items-center gap-3">
                          <span className="text-sm font-semibold uppercase">{c.broker_name}</span>
                          {c.connected ? (
                            <span className="flex items-center gap-1.5 text-xs font-medium text-emerald-400">
                              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                              Connected
                            </span>
                          ) : (
                            <span className="text-xs" style={{ color: "var(--muted)" }}>Not connected</span>
                          )}
                        </div>
                        {c.connected ? (
                          <button onClick={() => handleDisconnect(c.broker_name)} className="text-xs px-3 py-1.5 rounded-lg border hover:bg-white/5 transition-colors" style={{ borderColor: "var(--border)" }}>Disconnect</button>
                        ) : (
                          <button onClick={() => setBrokerModal(true)} className="text-xs px-3 py-1.5 rounded-lg border hover:bg-white/5 text-blue-400 transition-colors" style={{ borderColor: "var(--border)" }}>Connect</button>
                        )}
                      </div>
                      {/* Details (only when connected) */}
                      {c.connected && (
                        <div className="px-4 pb-3 pt-1 space-y-1.5">
                          <div className="flex items-center gap-6 text-xs" style={{ color: "var(--muted)" }}>
                            {c.account_id && (
                              <span>Login: <span className="font-medium" style={{ color: "var(--foreground)" }}>{c.account_id}</span></span>
                            )}
                            {c.server && (
                              <span>Server: <span className="font-medium" style={{ color: "var(--foreground)" }}>{c.server}</span></span>
                            )}
                          </div>
                          <div className="flex items-center gap-6 text-xs" style={{ color: "var(--muted)" }}>
                            {c.balance !== null && (
                              <span>Balance: <span className="font-medium text-emerald-400">${fmt(c.balance)} {c.currency}</span></span>
                            )}
                            {c.connected_since && (
                              <span>Uptime: <span className="font-medium" style={{ color: "var(--foreground)" }}>{fmtUptime(c.connected_since)}</span></span>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                  {connections.length === 0 && (
                    <p className="text-sm py-2" style={{ color: "var(--muted)" }}>No brokers configured</p>
                  )}
                </div>
              </Card>

              {/* Broker Help */}
              <Card>
                <h3 className="text-sm font-medium mb-2">Need a broker account?</h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <a href="https://www.oanda.com/register/" target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-2 px-3 py-2.5 text-sm rounded-lg border border-l-2 hover:bg-white/5 transition-all hover:shadow-[0_0_15px_rgba(139,92,246,0.08)] group"
                    style={{ borderColor: "var(--border)", borderLeftColor: "#8b5cf6" }}>
                    <span className="flex-1">
                      <span className="font-medium text-xs">Create Oanda Practice Account</span>
                      <span className="block text-[10px] mt-0.5" style={{ color: "var(--muted)" }}>Recommended for paper trading</span>
                    </span>
                    <svg className="w-3.5 h-3.5 opacity-50 group-hover:opacity-100 transition-opacity" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
                  </a>
                  <a href="https://www.ctrader.com" target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-2 px-3 py-2.5 text-sm rounded-lg border border-l-2 hover:bg-white/5 transition-all hover:shadow-[0_0_15px_rgba(59,130,246,0.08)] group"
                    style={{ borderColor: "var(--border)", borderLeftColor: "#3b82f6" }}>
                    <span className="flex-1">
                      <span className="font-medium text-xs">Create cTrader Account</span>
                      <span className="block text-[10px] mt-0.5" style={{ color: "var(--muted)" }}>Multi-asset trading platform</span>
                    </span>
                    <svg className="w-3.5 h-3.5 opacity-50 group-hover:opacity-100 transition-opacity" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
                  </a>
                </div>
              </Card>

              {/* News API Keys */}
              <Card>
                <h3 className="text-sm font-medium mb-1">News API Keys</h3>
                <p className="text-xs mb-4" style={{ color: "var(--muted)" }}>
                  Used by the news filter to block trades around high-impact events. Keys are stored per-user in your settings.
                </p>
                <div className="space-y-3">
                  {(["finnhub", "alphavantage", "newsapi"] as const).map((provider) => {
                    const labels: Record<string, string> = { finnhub: "Finnhub", alphavantage: "AlphaVantage", newsapi: "NewsAPI" };
                    const placeholders: Record<string, string> = { finnhub: "pk_...", alphavantage: "...", newsapi: "..." };
                    const visible = showKey[provider];
                    return (
                      <div key={provider}>
                        <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>{labels[provider]}</label>
                        <div className="relative">
                          <input
                            type={visible ? "text" : "password"}
                            value={apiKeys[provider]}
                            onChange={(e) => setApiKeys({ ...apiKeys, [provider]: e.target.value })}
                            placeholder={apiKeys[provider] ? "••••••••••••" : placeholders[provider]}
                            className="w-full px-3 py-2 pr-10 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                            style={{ borderColor: "var(--border)" }}
                          />
                          <button type="button" onClick={() => setShowKey((k) => ({ ...k, [provider]: !k[provider] }))}
                            className="absolute right-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted)" }}>
                            {visible ? <EyeOff size={14} /> : <Eye size={14} />}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                  <button onClick={handleSaveApiKeys}
                    className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white">
                    Save API Keys
                  </button>
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
          label: "Providers",
          content: (
            <div className="space-y-4">
              {/* Add Provider */}
              <Card>
                <h3 className="text-sm font-medium mb-3 flex items-center gap-2"><Database size={14} /> Add Market Data Provider</h3>
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Provider</label>
                      <select value={newProvider.name} onChange={(e) => setNewProvider({ ...newProvider, name: e.target.value })}
                        className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                        <option value="databento">Databento</option>
                        <option value="alphavantage">Alpha Vantage</option>
                        <option value="finnhub">Finnhub</option>
                        <option value="polygon">Polygon</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Data Type</label>
                      <select value={newProvider.type} onChange={(e) => setNewProvider({ ...newProvider, type: e.target.value })}
                        className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                        <option value="ohlcv">OHLCV</option>
                        <option value="tick">Tick Data</option>
                      </select>
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>API Key</label>
                    <input type="password" value={newProvider.key} onChange={(e) => setNewProvider({ ...newProvider, key: e.target.value })}
                      className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
                      style={{ borderColor: "var(--border)" }} placeholder="Enter API key" />
                  </div>
                  <button
                    disabled={!newProvider.key.trim()}
                    onClick={async () => {
                      try {
                        await api.post("/api/market-data/providers", { provider_name: newProvider.name, api_key: newProvider.key, data_type: newProvider.type });
                        toast.success(`${newProvider.name} provider added`);
                        setNewProvider({ ...newProvider, key: "" });
                        api.get("/api/market-data/providers").then((r) => setProviders(r.data));
                      } catch (e) { toast.error(getErrorMessage(e)); }
                    }}
                    className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 flex items-center gap-2"
                  >
                    <Plus size={14} /> Add Provider
                  </button>
                </div>
              </Card>

              {/* Existing Providers */}
              {providers.length > 0 && (
                <Card>
                  <h3 className="text-sm font-medium mb-3">Connected Providers</h3>
                  <div className="space-y-3">
                    {providers.map((p) => (
                      <div key={p.id} className="flex items-center justify-between py-2 border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                        <div>
                          <p className="text-sm font-medium capitalize">{p.provider_name}</p>
                          <p className="text-xs" style={{ color: "var(--muted)" }}>{p.api_key_masked} &middot; {p.data_type.toUpperCase()}</p>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            disabled={providerTesting === p.id}
                            onClick={async () => {
                              setProviderTesting(p.id);
                              try {
                                const r = await api.post(`/api/market-data/providers/${p.id}/test`);
                                if (r.data.status === "ok") toast.success(r.data.message);
                                else toast.error(r.data.message);
                              } catch { toast.error("Test failed"); }
                              setProviderTesting(null);
                            }}
                            className="px-2 py-1 text-xs rounded border hover:bg-white/5" style={{ borderColor: "var(--border)" }}
                          >
                            {providerTesting === p.id ? <Loader2 size={12} className="animate-spin" /> : "Test"}
                          </button>
                          <button
                            onClick={async () => {
                              await api.delete(`/api/market-data/providers/${p.id}`);
                              toast.success("Provider removed");
                              setProviders(providers.filter((x) => x.id !== p.id));
                            }}
                            className="p-1 text-red-400 hover:bg-red-500/10 rounded"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              )}

              {/* Request Other Provider */}
              <Card>
                <h3 className="text-sm font-medium mb-2">Need a different provider?</h3>
                <p className="text-xs mb-3" style={{ color: "var(--muted)" }}>Request support for a provider not in the list.</p>
                <button
                  onClick={async () => {
                    const name = prompt("Which market data provider would you like us to add?");
                    if (name) {
                      try {
                        await api.post("/api/feedback", { feedback_type: "provider_request", message: `Please add support for: ${name}` });
                        toast.success("Request submitted!");
                      } catch { toast.error("Failed to submit"); }
                    }
                  }}
                  className="px-3 py-1.5 text-xs rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}
                >
                  Request Provider
                </button>
              </Card>
            </div>
          ),
        },
        {
          label: "Feedback",
          content: (
            <div className="space-y-4">
              <Card>
                <h3 className="text-sm font-medium mb-3 flex items-center gap-2"><MessageSquare size={14} /> Submit Feedback</h3>
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Type</label>
                    <select value={feedbackType} onChange={(e) => setFeedbackType(e.target.value)}
                      className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                      <option value="bug">Bug Report</option>
                      <option value="feature">Feature Request</option>
                      <option value="provider_request">Provider Request</option>
                      <option value="other">Other</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Message</label>
                    <textarea value={feedbackMsg} onChange={(e) => setFeedbackMsg(e.target.value)} rows={4}
                      className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500 resize-none"
                      style={{ borderColor: "var(--border)" }}
                      placeholder="Describe the issue or feature you'd like..." />
                  </div>
                  <button
                    disabled={!feedbackMsg.trim() || feedbackSending}
                    onClick={async () => {
                      setFeedbackSending(true);
                      try {
                        await api.post("/api/feedback", { feedback_type: feedbackType, message: feedbackMsg });
                        toast.success("Feedback submitted!");
                        setFeedbackMsg("");
                      } catch (e) { toast.error(getErrorMessage(e)); }
                      setFeedbackSending(false);
                    }}
                    className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 flex items-center gap-2"
                  >
                    {feedbackSending ? <Loader2 size={14} className="animate-spin" /> : null}
                    Submit Feedback
                  </button>
                </div>
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
                  <div className="flex items-center justify-between py-2 border-t" style={{ borderColor: "var(--border)" }}>
                    <div>
                      <p className="text-sm">Backtest Results</p>
                      <p className="text-xs" style={{ color: "var(--muted)" }}>Export last backtest results as JSON</p>
                    </div>
                    <button onClick={() => downloadJSON("/api/backtest/results", "backtest_results.json")}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>
                      <Download size={14} /> Download
                    </button>
                  </div>
                </div>
              </Card>

              {/* Recycle Bin */}
              <Card>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-medium flex items-center gap-2">
                    <Trash2 size={16} style={{ color: "var(--muted)" }} /> Recycle Bin
                  </h3>
                  {recycleBin.length > 0 && (
                    <button onClick={handlePurgeAll} className="text-xs text-red-400 hover:text-red-300">Empty Recycle Bin</button>
                  )}
                </div>
                {recycleBin.length === 0 ? (
                  <p className="text-sm py-2" style={{ color: "var(--muted)" }}>Recycle bin is empty</p>
                ) : (
                  <div className="space-y-2">
                    {recycleBin.map((item) => (
                      <div key={item.id} className="flex items-center justify-between px-3 py-2 rounded-lg border" style={{ borderColor: "var(--border)" }}>
                        <div>
                          <span className="text-sm">{item.name}</span>
                          <span className="text-xs ml-2" style={{ color: "var(--muted)" }}>{item.symbol}</span>
                          <span className="text-xs ml-2" style={{ color: "var(--muted)" }}>Deleted {item.deleted_at ? new Date(item.deleted_at).toLocaleDateString() : ""}</span>
                        </div>
                        <div className="flex gap-2">
                          <button onClick={() => handleRestore(item.id)} className="text-xs px-2 py-1 rounded border hover:bg-white/5 text-blue-400" style={{ borderColor: "var(--border)" }}>Restore</button>
                          <button onClick={() => handlePurge(item.id)} className="text-xs px-2 py-1 rounded border border-red-500/30 text-red-400 hover:bg-red-500/10">Delete Forever</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
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
                onConfirm={handleClearLogs}
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
