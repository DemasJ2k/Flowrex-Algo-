"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import Card, { StatCard } from "@/components/ui/Card";
import StatusBadge from "@/components/ui/StatusBadge";
import DataTable, { Column } from "@/components/ui/DataTable";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import { Loader2, Copy, Plus, Shield, Users, CheckCircle, XCircle, MessageSquare, Inbox } from "lucide-react";

interface InviteCode { id: number; code: string; is_active: boolean; max_uses: number; use_count: number; status: string; created_at: string; }
interface AdminUser { id: number; email: string; is_admin: boolean; created_at: string; }
interface SystemHealth { database: string; running_agents: number[]; websocket_connections: number; }
interface AccessRequest { id: number; name: string; email: string; phone: string | null; message: string | null; status: string; created_at: string; }
interface FeedbackItem { id: number; user_id: number | null; type: string; message: string; status: string; created_at: string; }

export default function AdminPage() {
  const [invites, setInvites] = useState<InviteCode[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [system, setSystem] = useState<SystemHealth | null>(null);
  const [accessRequests, setAccessRequests] = useState<AccessRequest[]>([]);
  const [feedback, setFeedback] = useState<FeedbackItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [revokeConfirm, setRevokeConfirm] = useState<number | null>(null);
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);

  // Check admin status first
  useEffect(() => {
    api.get("/api/auth/me").then((r) => {
      setIsAdmin(r.data?.is_admin || false);
      if (!r.data?.is_admin) window.location.href = "/";
    }).catch(() => { window.location.href = "/login"; });
  }, []);

  const fetchData = () => {
    Promise.all([
      api.get("/api/admin/invites").then((r) => setInvites(r.data)).catch((e) => console.warn("fetch failed:", e?.message)),
      api.get("/api/admin/users").then((r) => setUsers(r.data)).catch((e) => console.warn("fetch failed:", e?.message)),
      api.get("/api/admin/system").then((r) => setSystem(r.data)).catch((e) => console.warn("fetch failed:", e?.message)),
      api.get("/api/admin/access-requests").then((r) => setAccessRequests(r.data)).catch((e) => console.warn("fetch failed:", e?.message)),
      api.get("/api/admin/feedback").then((r) => setFeedback(r.data)).catch((e) => console.warn("fetch failed:", e?.message)),
    ]).finally(() => setLoading(false));
  };
  useEffect(() => { if (isAdmin) fetchData(); }, [isAdmin]);

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
      setRevokeConfirm(null);
      fetchData();
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleApproveRequest = async (id: number) => {
    try {
      await api.post("/api/admin/access-requests/" + id + "/approve");
      toast.success("Access request approved");
      fetchData();
    } catch (e: unknown) { toast.error(getErrorMessage(e)); }
  };

  const handleRejectRequest = async (id: number) => {
    try {
      await api.post("/api/admin/access-requests/" + id + "/reject");
      toast.success("Access request rejected");
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
      revokeConfirm === r.id ? (
        <span className="flex items-center gap-1">
          <span className="text-xs text-yellow-400">Revoke?</span>
          <button onClick={() => handleRevoke(r.id)} className="text-xs text-red-400 hover:text-red-300 font-medium">Yes</button>
          <button onClick={() => setRevokeConfirm(null)} className="text-xs hover:text-white" style={{ color: "var(--muted)" }}>No</button>
        </span>
      ) : (
        <button onClick={() => setRevokeConfirm(r.id)} className="text-xs text-red-400 hover:text-red-300">Revoke</button>
      )
    ) : null },
  ];

  // Guard: don't render admin content until auth check completes (security fix)
  if (isAdmin === null || loading) return <div className="flex items-center justify-center h-64"><Loader2 size={24} className="animate-spin" style={{ color: "var(--muted)" }} /></div>;
  if (isAdmin === false) return null; // redirect already in progress

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold bg-gradient-to-r from-violet-400 to-blue-400 bg-clip-text text-transparent">Admin</h1>

      {/* System Health */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          <StatCard key="db" label="Database" value={system?.database === "connected" ? "Connected" : "Disconnected"} color={system?.database === "connected" ? "green" : "red"} />,
          <StatCard key="ag" label="Active Agents" value={system?.running_agents?.length || 0} />,
          <StatCard key="ws" label="WebSocket" value={system?.websocket_connections || 0} sub="connections" />,
          <StatCard key="us" label="Users" value={users.length} />,
        ].map((card, i) => (
          <div key={i} className="animate-fade-in" style={{ animationDelay: `${i * 0.06}s` }}>
            {card}
          </div>
        ))}
      </div>

      {/* Invite Codes */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium flex items-center gap-2"><Shield size={16} /> Invite Codes</h2>
          <div className="flex gap-2">
            <button onClick={() => handleGenerate(1)} disabled={generating} className="px-3 py-1.5 text-xs rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>+1</button>
            <button onClick={() => handleGenerate(5)} disabled={generating} className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg btn-gradient text-white disabled:opacity-50">
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
            <div key={u.id} className={`flex items-center justify-between px-3 py-2 rounded-lg border border-l-2 ${u.is_admin ? "!border-l-violet-500" : "!border-l-blue-500/40"}`} style={{ borderColor: "var(--border)" }}>
              <div className="flex items-center gap-2">
                <span className="text-sm">{u.email}</span>
                {u.is_admin && <StatusBadge value="admin" />}
              </div>
              <span className="text-xs" style={{ color: "var(--muted)" }}>{u.created_at ? new Date(u.created_at).toLocaleDateString() : ""}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Access Requests */}
      <Card>
        <h2 className="text-sm font-medium mb-3 flex items-center gap-2"><Inbox size={16} /> Access Requests</h2>
        {accessRequests.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--muted)" }}>No access requests</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b" style={{ borderColor: "var(--border)" }}>
                  <th className="text-left py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>Name</th>
                  <th className="text-left py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>Email</th>
                  <th className="text-left py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>Phone</th>
                  <th className="text-left py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>Message</th>
                  <th className="text-left py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>Status</th>
                  <th className="text-left py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>Date</th>
                  <th className="text-right py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {accessRequests.map((r) => (
                  <tr key={r.id} className="border-b" style={{ borderColor: "var(--border)" }}>
                    <td className="py-2 px-2">{r.name}</td>
                    <td className="py-2 px-2">{r.email}</td>
                    <td className="py-2 px-2">{r.phone || "\u2014"}</td>
                    <td className="py-2 px-2 max-w-[200px] truncate" title={r.message || ""}>{r.message || "\u2014"}</td>
                    <td className="py-2 px-2"><StatusBadge value={r.status} /></td>
                    <td className="py-2 px-2">{r.created_at ? new Date(r.created_at).toLocaleDateString() : ""}</td>
                    <td className="py-2 px-2 text-right">
                      {r.status === "pending" ? (
                        <span className="flex items-center justify-end gap-2">
                          <button onClick={() => handleApproveRequest(r.id)} className="flex items-center gap-1 text-emerald-400 hover:text-emerald-300"><CheckCircle size={12} /> Approve</button>
                          <button onClick={() => handleRejectRequest(r.id)} className="flex items-center gap-1 text-red-400 hover:text-red-300"><XCircle size={12} /> Reject</button>
                        </span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Feedback */}
      <Card>
        <h2 className="text-sm font-medium mb-3 flex items-center gap-2"><MessageSquare size={16} /> Feedback</h2>
        {feedback.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--muted)" }}>No feedback reports</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b" style={{ borderColor: "var(--border)" }}>
                  <th className="text-left py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>User ID</th>
                  <th className="text-left py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>Type</th>
                  <th className="text-left py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>Message</th>
                  <th className="text-left py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>Status</th>
                  <th className="text-left py-2 px-2 font-medium" style={{ color: "var(--muted)" }}>Date</th>
                </tr>
              </thead>
              <tbody>
                {feedback.map((f) => (
                  <tr key={f.id} className="border-b" style={{ borderColor: "var(--border)" }}>
                    <td className="py-2 px-2">{f.user_id ?? "Public"}</td>
                    <td className="py-2 px-2"><StatusBadge value={f.type} /></td>
                    <td className="py-2 px-2 max-w-[300px] truncate" title={f.message}>{f.message}</td>
                    <td className="py-2 px-2"><StatusBadge value={f.status} /></td>
                    <td className="py-2 px-2">{f.created_at ? new Date(f.created_at).toLocaleDateString() : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
