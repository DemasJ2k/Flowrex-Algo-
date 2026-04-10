"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import api from "@/lib/api";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";

const BROKER_FIELDS: Record<string, { label: string; key: string; type?: string }[]> = {
  oanda: [
    { label: "API Key", key: "api_key" },
    { label: "Account ID", key: "account_id" },
    { label: "Practice Account", key: "practice", type: "toggle" },
  ],
  ctrader: [
    { label: "Client ID", key: "client_id" },
    { label: "Client Secret", key: "client_secret" },
    { label: "Access Token", key: "access_token" },
    { label: "Account ID", key: "account_id" },
  ],
  mt5: [
    { label: "Login", key: "login" },
    { label: "Password", key: "password" },
    { label: "Server", key: "server" },
  ],
  tradovate: [
    { label: "Username", key: "username" },
    { label: "Password", key: "password" },
    { label: "App ID", key: "app_id" },
    { label: "CID", key: "cid" },
    { label: "Secret", key: "sec" },
    { label: "Demo Account", key: "demo", type: "toggle" },
  ],
};

export default function BrokerModal({
  open,
  onClose,
  onConnected,
}: {
  open: boolean;
  onClose: () => void;
  onConnected: () => void;
}) {
  const [broker, setBroker] = useState("oanda");
  const [creds, setCreds] = useState<Record<string, string | boolean>>({ practice: true });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleConnect = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.post("/api/broker/connect", { broker_name: broker, credentials: creds }, { timeout: 30000 });
      if (res.data.status === "connected") {
        toast.success(`Connected to ${broker.toUpperCase()}`);
        onConnected();
        onClose();
      } else {
        setError(res.data.message || "Connection failed");
      }
    } catch (e: unknown) {
      setError(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Connect Broker">
      {/* Broker Selection */}
      <div className="flex gap-2 mb-4">
        {["oanda", "tradovate", "ctrader", "mt5"].map((b) => (
          <button
            key={b}
            onClick={() => { setBroker(b); setCreds(b === "oanda" ? { practice: true } : b === "tradovate" ? { demo: true } : {}); }}
            className={`px-4 py-2 text-sm font-medium rounded-lg border transition-colors ${
              broker === b ? "border-blue-500 bg-blue-500/10 text-blue-400" : "hover:bg-white/5"
            }`}
            style={{ borderColor: broker === b ? undefined : "var(--border)" }}
          >
            {b.toUpperCase()}
          </button>
        ))}
      </div>

      {/* Dynamic Fields */}
      <div className="space-y-3">
        {BROKER_FIELDS[broker]?.map((field) =>
          field.type === "toggle" ? (
            <label key={field.key} className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={creds[field.key] === true}
                onChange={(e) => setCreds({ ...creds, [field.key]: e.target.checked })}
                className="rounded"
              />
              {field.label}
            </label>
          ) : (
            <div key={field.key}>
              <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>
                {field.label}
              </label>
              <input
                type={field.key.includes("password") || field.key.includes("secret") ? "password" : "text"}
                value={(creds[field.key] as string) || ""}
                onChange={(e) => setCreds({ ...creds, [field.key]: e.target.value })}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500 transition-colors"
                style={{ borderColor: "var(--border)" }}
              />
            </div>
          )
        )}
      </div>

      {error && <p className="text-red-400 text-sm mt-3">{error}</p>}

      <button
        onClick={handleConnect}
        disabled={loading}
        className="w-full mt-4 px-4 py-2.5 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 transition-colors"
      >
        {loading ? "Connecting..." : "Connect"}
      </button>
    </Modal>
  );
}
