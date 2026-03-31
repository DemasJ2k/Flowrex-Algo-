"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import Card, { StatCard } from "@/components/ui/Card";
import StatusBadge from "@/components/ui/StatusBadge";
import DataTable, { Column } from "@/components/ui/DataTable";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import { Loader2, Copy, Plus, Shield, Users } from "lucide-react";

interface InviteCode { id: number; code: string; is_active: boolean; max_uses: number; use_count: number; status: string; created_at: string; }
interface AdminUser { id: number; email: string; is_admin: boolean; created_at: string; }
interface SystemHealth { database: string; running_agents: number[]; websocket_connections: number; }

export default function AdminPage() {
  const [invites, setInvites] = useState<InviteCode[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [system, setSystem] = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);

  const fetchData = () => {
    Promise.all([
      api.get("/api/admin/invites").then((r) => setInvites(r.data)).catch(() => {}),
      api.get("/api/admin/users").then((r) => setUsers(r.data)).catch(() => {}),
      api.get("/api/admin/system").then((r) => setSystem(r.data)).catch(() => {}),
    ]).finally(() => setLoading(false));
  };
  useEffect(() => { fetchData(); }, []);

  const handleGenerate = async (count: number = 5) => {
    setGenerating(true);
    try {
      const res = await api.post("/api/admin/invites", { count });
      toast.success(res.data.codes.length + " invite codes generated");
      fetchData();
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
    finally { setGenerating(false); }
  };

  const handleCopy = (code: string) => {
    navigator.clipboard.writeText(code);
    toast.success("Copied: " + code);
  };

  const handleRevoke = async (id: number) => {
    try {
      await api.delete("/api/admin/invites/" + id);
      toast.success("Invite revoked");
      fetchData();
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const inviteCols: Column<InviteCode>[] = [
    { header: "Code", key: "code", render: (r) => (
      <button onClick={() => handleCopy(r.code)} className="flex items-center gap-1 text-xs font-mono hover:text-blue-400" title="Click to copy">
        {r.code} <Copy size={10} />
      </button>
    )},
    { header: "Status", key: "status", render: (r) => <StatusBadge value={r.status} /> },
    { header: "Uses", key: "use_count", render: (r) => r.use_count + "/" + r.max_uses },
    { header: "Created", key: "created_at", render: (r) => r.created_at ? new Date(r.created_at).toLocaleDateString() : "" },
    { header: "", key: "action", sortable: false, render: (r) => r.status === "active" ? (
      <button onClick={() => handleRevoke(r.id)} className="text-xs text-red-400 hover:text-red-300">Revoke</button>
    ) : null },
  ];

  if (loading) return <div className="flex items-center justify-center h-64"><Loader2 size={24} className="animate-spin" style={{ color: "var(--muted)" }} /></div>;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Admin</h1>

      {/* System Health */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Database" value={system?.database === "connected" ? "Connected" : "Disconnected"} color={system?.database === "connected" ? "green" : "red"} />
        <StatCard label="Active Agents" value={system?.running_agents?.length || 0} />
        <StatCard label="WebSocket" value={system?.websocket_connections || 0} sub="connections" />
        <StatCard label="Users" value={users.length} />
      </div>

      {/* Invite Codes */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium flex items-center gap-2"><Shield size={16} /> Invite Codes</h2>
          <div className="flex gap-2">
            <button onClick={() => handleGenerate(1)} disabled={generating} className="px-3 py-1.5 text-xs rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>+1</button>
            <button onClick={() => handleGenerate(5)} disabled={generating} className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">
              <Plus size={12} /> Generate 5
            </button>
          </div>
        </div>
        <DataTable columns={inviteCols as unknown as Column<Record<string, unknown>>[]} data={invites as unknown as Record<string, unknown>[]} emptyMessage="No invite codes" paginated pageSize={10} />
      </Card>

      {/* Users */}
      <Card>
        <h2 className="text-sm font-medium mb-3 flex items-center gap-2"><Users size={16} /> Users</h2>
        <div className="space-y-2">
          {users.map((u) => (
            <div key={u.id} className="flex items-center justify-between px-3 py-2 rounded-lg border" style={{ borderColor: "var(--border)" }}>
              <div className="flex items-center gap-2">
                <span className="text-sm">{u.email}</span>
                {u.is_admin && <StatusBadge value="admin" />}
              </div>
              <span className="text-xs" style={{ color: "var(--muted)" }}>{u.created_at ? new Date(u.created_at).toLocaleDateString() : ""}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
