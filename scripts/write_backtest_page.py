"""Write the updated Backtest page."""
import os

TARGET = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "app", "backtest", "page.tsx")

CONTENT = r""""use client";

import { useState } from "react";
import Card, { StatCard } from "@/components/ui/Card";
import DataTable, { Column } from "@/components/ui/DataTable";
import StatusBadge from "@/components/ui/StatusBadge";
import EquityCurveChart from "@/components/EquityCurveChart";
import api from "@/lib/api";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import { FlaskConical, Loader2, TrendingDown } from "lucide-react";

export default function BacktestPage() {
  const [symbol, setSymbol] = useState("XAUUSD");
  const [agentType, setAgentType] = useState("scalping");
  const [risk, setRisk] = useState(0.005);
  const [spreadPips, setSpreadPips] = useState("");
  const [slippagePips, setSlippagePips] = useState("");
  const [commission, setCommission] = useState("0");
  const [primeHours, setPrimeHours] = useState(true);
  const [monteCarlo, setMonteCarlo] = useState(true);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  const fmt = (v: number | undefined) => v !== undefined ? v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "\u2014";
  const pnlColor = (v: number) => v >= 0 ? "green" : "red";

  const runBacktest = async () => {
    setLoading(true);
    setResult(null);
    try {
      const body: Record<string, unknown> = {
        symbol, agent_type: agentType, risk_per_trade: risk,
        prime_hours_only: primeHours, include_monte_carlo: monteCarlo,
      };
      if (spreadPips) body.spread_pips = parseFloat(spreadPips);
      if (slippagePips) body.slippage_pips = parseFloat(slippagePips);
      if (commission) body.commission_per_lot = parseFloat(commission);

      await api.post("/api/backtest/run", body);
      toast.success("Backtest started for " + symbol);

      const poll = setInterval(async () => {
        try {
          const res = await api.get("/api/backtest/results");
          if (!res.data.running.active && res.data.results[symbol]) {
            clearInterval(poll);
            setResult(res.data.results[symbol]);
            setLoading(false);
          }
        } catch { /* keep polling */ }
      }, 2000);
      setTimeout(() => { clearInterval(poll); setLoading(false); }, 600000);
    } catch (e: unknown) {
      toast.error(getErrorMessage(e));
      setLoading(false);
    }
  };

  const mc = result?.monte_carlo as Record<string, number> | null;
  const eqCurve = ((result?.equity_curve || []) as Array<{time: number; pnl: number}>)
    .map((p) => ({ time: p.time, value: p.pnl }));
  const ddCurve = ((result?.drawdown_curve || []) as Array<{time: number; drawdown: number}>)
    .map((p) => ({ time: p.time, value: -p.drawdown }));

  const tradeCols: Column<Record<string, unknown>>[] = [
    { header: "Side", key: "direction", render: (r) => <StatusBadge value={r.direction as string} /> },
    { header: "Entry", key: "entry_price", align: "right", render: (r) => fmt(r.entry_price as number) },
    { header: "Exit", key: "exit_price", align: "right", render: (r) => fmt(r.exit_price as number) },
    { header: "Lots", key: "lot_size", align: "right" },
    { header: "Gross", key: "gross_pnl", align: "right", render: (r) => {
      const v = r.gross_pnl as number;
      return <span className={v >= 0 ? "text-emerald-400" : "text-red-400"}>{fmt(v)}</span>;
    }},
    { header: "Net P&L", key: "pnl", align: "right", render: (r) => {
      const v = r.pnl as number;
      return <span className={v >= 0 ? "text-emerald-400" : "text-red-400"}>{fmt(v)}</span>;
    }},
    { header: "Reason", key: "exit_reason", render: (r) => <StatusBadge value={r.exit_reason as string} /> },
    { header: "Bars", key: "duration_bars", align: "right" },
  ];

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Backtest</h1>

      {/* Configuration */}
      <Card>
        <h2 className="text-sm font-medium mb-4">Configuration</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Symbol</label>
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
              {["XAUUSD", "BTCUSD", "US30", "ES", "NAS100"].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Strategy</label>
            <select value={agentType} onChange={(e) => setAgentType(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
              <option value="scalping">Scalping</option>
              <option value="expert">Expert</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Risk (%)</label>
            <input type="number" step="0.1" min="0.1" max="3" value={(risk * 100).toFixed(1)}
              onChange={(e) => setRisk(parseFloat(e.target.value) / 100)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)" }} />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Spread (pips)</label>
            <input type="number" step="0.1" value={spreadPips} onChange={(e) => setSpreadPips(e.target.value)} placeholder="Auto"
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)" }} />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Slippage (pips)</label>
            <input type="number" step="0.1" value={slippagePips} onChange={(e) => setSlippagePips(e.target.value)} placeholder="Auto"
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)" }} />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Commission/lot ($)</label>
            <input type="number" step="0.5" value={commission} onChange={(e) => setCommission(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent" style={{ borderColor: "var(--border)" }} />
          </div>
        </div>
        <div className="flex items-center gap-4 mt-3">
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={primeHours} onChange={(e) => setPrimeHours(e.target.checked)} className="rounded" />
            Prime hours only
          </label>
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={monteCarlo} onChange={(e) => setMonteCarlo(e.target.checked)} className="rounded" />
            Monte Carlo (1000 sims)
          </label>
        </div>
        <button onClick={runBacktest} disabled={loading}
          className="mt-4 px-6 py-2.5 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 flex items-center gap-2">
          {loading ? <><Loader2 size={14} className="animate-spin" /> Running...</> : <><FlaskConical size={14} /> Run Backtest</>}
        </button>
      </Card>

      {/* Results */}
      {result && !result.error && (
        <>
          {/* Summary Stats Row 1 */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatCard label="Gross P&L" value={fmt(result.gross_pnl as number)} color={pnlColor(result.gross_pnl as number)} />
            <StatCard label="Net P&L" value={fmt(result.net_pnl as number)} color={pnlColor(result.net_pnl as number)} />
            <StatCard label="Total Costs" value={fmt(result.total_costs as number)} color="red" />
            <StatCard label="Win Rate" value={(result.win_rate as number).toFixed(1) + "%"} sub={(result.winning_trades as number) + "W / " + (result.losing_trades as number) + "L"} />
            <StatCard label="Profit Factor" value={fmt(result.profit_factor as number)} />
            <StatCard label="Trades" value={result.total_trades as number} />
          </div>
          {/* Summary Stats Row 2 */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatCard label="Sharpe" value={fmt(result.sharpe_ratio as number)} />
            <StatCard label="Max Drawdown" value={fmt(result.max_drawdown as number)} color="red" />
            <StatCard label="Expectancy" value={fmt(result.expectancy as number)} color={pnlColor(result.expectancy as number)} />
            <StatCard label="Risk:Reward" value={fmt(result.risk_reward_ratio as number)} />
            <StatCard label="Avg Win" value={fmt(result.avg_win as number)} color="green" />
            <StatCard label="Avg Loss" value={fmt(result.avg_loss as number)} color="red" />
          </div>

          {/* Cost Breakdown */}
          <Card>
            <h3 className="text-sm font-medium mb-2">Cost Breakdown</h3>
            <div className="flex flex-wrap gap-4 text-xs" style={{ color: "var(--muted)" }}>
              <span>Spread: <span className="text-red-400">${fmt(result.total_spread_cost as number)}</span></span>
              <span>Slippage: <span className="text-red-400">${fmt(result.total_slippage_cost as number)}</span></span>
              <span>Commission: <span className="text-red-400">${fmt(result.total_commission as number)}</span></span>
              <span>Total: <span className="text-red-400 font-medium">${fmt(result.total_costs as number)}</span></span>
              <span>Win Streak: {result.max_consecutive_wins as number}</span>
              <span>Loss Streak: {result.max_consecutive_losses as number}</span>
              <span>Avg Duration: {(result.avg_trade_duration_bars as number).toFixed(0)} bars</span>
            </div>
          </Card>

          {/* Equity + Drawdown Charts */}
          {eqCurve.length >= 2 && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <Card>
                <h3 className="text-sm font-medium mb-2">Equity Curve</h3>
                <EquityCurveChart data={eqCurve} height={180} />
              </Card>
              <Card>
                <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
                  <TrendingDown size={14} style={{ color: "var(--muted)" }} /> Drawdown
                </h3>
                <EquityCurveChart data={ddCurve} height={180} />
              </Card>
            </div>
          )}

          {/* Monte Carlo Results */}
          {mc && (
            <Card>
              <h3 className="text-sm font-medium mb-2">Monte Carlo Analysis ({mc.simulations} simulations)</h3>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                <StatCard label="DD 95th %" value={fmt(mc.drawdown_95th)} color="red" />
                <StatCard label="DD 99th %" value={fmt(mc.drawdown_99th)} color="red" />
                <StatCard label="Worst DD" value={fmt(mc.worst_drawdown)} color="red" />
                <StatCard label="Median P&L" value={fmt(mc.median_pnl)} color={pnlColor(mc.median_pnl)} />
                <StatCard label="P&L 5th %" value={fmt(mc.pnl_5th)} color={pnlColor(mc.pnl_5th)} />
                <StatCard label="P&L 95th %" value={fmt(mc.pnl_95th)} color={pnlColor(mc.pnl_95th)} />
              </div>
            </Card>
          )}

          {/* Monthly Returns */}
          {result.monthly_returns && Object.keys(result.monthly_returns as Record<string, number>).length > 0 && (
            <Card>
              <h3 className="text-sm font-medium mb-2">Monthly Returns</h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(result.monthly_returns as Record<string, number>).map(([month, pnl]) => (
                  <div key={month} className="px-3 py-2 rounded-lg border text-xs text-center min-w-[80px]" style={{ borderColor: "var(--border)" }}>
                    <div style={{ color: "var(--muted)" }}>{month}</div>
                    <div className={pnl >= 0 ? "text-emerald-400 font-medium" : "text-red-400 font-medium"}>{pnl >= 0 ? "+" : ""}{fmt(pnl)}</div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* Trade Table */}
          {(result.trades as Array<Record<string, unknown>>)?.length > 0 && (
            <Card>
              <h3 className="text-sm font-medium mb-2">Trades ({(result.trades as Array<unknown>).length})</h3>
              <DataTable columns={tradeCols} data={result.trades as Record<string, unknown>[]} paginated pageSize={25} emptyMessage="No trades" />
            </Card>
          )}
        </>
      )}

      {result?.error && (
        <Card><p className="text-red-400 text-sm">{result.error as string}</p></Card>
      )}
    </div>
  );
}
"""

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(CONTENT)
print(f"Written {len(CONTENT)} chars to {TARGET}")
