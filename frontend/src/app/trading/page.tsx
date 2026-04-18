"use client";

import { debugWarn } from "@/lib/debug";
import { useEffect, useState, useCallback, useRef } from "react";
import api from "@/lib/api";
import CandlestickChart, { ChartIndicators, ChartMarker } from "@/components/CandlestickChart";
import SearchableSelect from "@/components/ui/SearchableSelect";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import AgentPanel from "@/components/AgentPanel";
import BrokerModal from "@/components/BrokerModal";
import AgentWizard from "@/components/AgentWizard";
import OrderPanel from "@/components/OrderPanel";
import Glass from "@/components/ui/Glass";
import Tabs from "@/components/ui/Tabs";
import DataTable, { Column } from "@/components/ui/DataTable";
import StatusBadge from "@/components/ui/StatusBadge";
import type { AccountInfo, BrokerStatus, CandleData, LivePosition, LiveOrder, AgentTrade, EngineLog, PnlSummaryItem } from "@/types";
import { Plug, Plus, ShoppingCart, Loader2, SlidersHorizontal, Database, Activity, Gauge } from "lucide-react";
import Card from "@/components/ui/Card";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import { toSydneyTime } from "@/lib/timezone";
import { useWebSocket, WSMessage } from "@/hooks/useWebSocket";
import WSStatusBadge from "@/components/WSStatusBadge";

const BROKER_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];
const DATABENTO_TIMEFRAMES = ["1s", "M1", "M5", "M15", "M30", "H1", "H4", "D1"];
const SYMBOLS = ["XAUUSD", "BTCUSD", "US30", "ES", "NAS100", "EURUSD", "GBPUSD"];

export default function TradingPage() {
  const [symbol, setSymbol] = useState("XAUUSD");
  const [timeframe, setTimeframe] = useState("M5");
  const [broker, setBroker] = useState<BrokerStatus>({ connected: false, broker: null });
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [candles, setCandles] = useState<CandleData[]>([]);
  const [positions, setPositions] = useState<LivePosition[]>([]);
  const [orders, setOrders] = useState<LiveOrder[]>([]);
  const [trades, setTrades] = useState<AgentTrade[]>([]);
  const [engineLogs, setEngineLogs] = useState<EngineLog[]>([]);
  const [pnlSummary, setPnlSummary] = useState<PnlSummaryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [brokerModal, setBrokerModal] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [orderOpen, setOrderOpen] = useState(false);
  const [liveBid, setLiveBid] = useState<number | null>(null);
  const [liveAsk, setLiveAsk] = useState<number | null>(null);
  const [lastTickTime, setLastTickTime] = useState<number>(0);
  const [indicators, setIndicators] = useState<ChartIndicators>(() => {
    if (typeof window !== "undefined") {
      try { return JSON.parse(localStorage.getItem("chart_indicators") || "{}"); } catch { return {}; }
    }
    return {};
  });
  const [dataSource, setDataSource] = useState("broker");
  const [dataSources, setDataSources] = useState<Array<{name: string; label: string; symbols: string[] | string; has_ticks: boolean}>>([]);
  const [showTicks, setShowTicks] = useState(false);
  const [ticks, setTicks] = useState<Array<{time: number; price: number; size: number; side: string}>>([]);
  const [indicatorMenuOpen, setIndicatorMenuOpen] = useState(false);
  const [logFilter, setLogFilter] = useState("all");
  const [logSearch, setLogSearch] = useState("");
  const [confirmClose, setConfirmClose] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const fetchingRef = useRef(false);

  const fmt = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // Persist indicator selections
  const toggleIndicator = (key: keyof ChartIndicators) => {
    const updated = { ...indicators, [key]: !indicators[key] };
    setIndicators(updated);
    if (typeof window !== "undefined") localStorage.setItem("chart_indicators", JSON.stringify(updated));
  };

  // Compute trade markers for chart
  const chartMarkers: ChartMarker[] = trades
    .filter((t) => t.symbol === symbol && t.entry_time)
    .flatMap((t) => {
      const markers: ChartMarker[] = [];
      const entryTime = Math.floor(new Date(t.entry_time).getTime() / 1000);
      markers.push({
        time: entryTime,
        position: t.direction === "BUY" ? "belowBar" : "aboveBar",
        color: t.direction === "BUY" ? "#22c55e" : "#ef4444",
        shape: t.direction === "BUY" ? "arrowUp" : "arrowDown",
        text: t.direction,
      });
      if (t.exit_time && t.exit_reason) {
        markers.push({
          time: Math.floor(new Date(t.exit_time).getTime() / 1000),
          position: "aboveBar",
          color: "#71717a",
          shape: "circle",
          text: t.exit_reason,
        });
      }
      return markers;
    });

  // Filtered engine logs
  const filteredLogs = engineLogs.filter((l) => {
    if (logFilter !== "all" && l.level !== logFilter) return false;
    if (logSearch && !l.message.toLowerCase().includes(logSearch.toLowerCase())) return false;
    return true;
  });

  // History stats
  const historyStats = (() => {
    const closed = trades.filter((t) => t.status === "closed");
    const pnls = closed.map((t) => t.broker_pnl ?? t.pnl ?? 0);
    const wins = pnls.filter((p) => p > 0);
    const losses = pnls.filter((p) => p < 0);
    return {
      total: pnls.reduce((s, p) => s + p, 0),
      count: closed.length,
      winRate: closed.length > 0 ? (wins.length / closed.length * 100) : 0,
      avgWin: wins.length > 0 ? wins.reduce((s, p) => s + p, 0) / wins.length : 0,
      avgLoss: losses.length > 0 ? losses.reduce((s, p) => s + p, 0) / losses.length : 0,
    };
  })();

  // WebSocket for real-time data
  const handleWSMessage = useCallback((msg: WSMessage) => {
    if (msg.channel.startsWith("price:")) {
      const d = msg.data as { bid?: number; ask?: number; time?: number };
      if (d.bid) setLiveBid(d.bid);
      if (d.ask) setLiveAsk(d.ask);
      if (d.time) setLastTickTime(d.time as number);
    } else if (msg.channel === "account") {
      const d = msg.data as unknown as AccountInfo;
      setAccount(d);
    } else if (msg.channel.startsWith("agent:")) {
      const d = msg.data as { type?: string; data?: EngineLog };
      if (d.type === "log" && d.data) {
        setEngineLogs((prev) => [d.data as EngineLog, ...prev].slice(0, 100));
      }
    }
  }, []);

  const { status: wsStatus, subscribe: wsSub, unsubscribe: wsUnsub } = useWebSocket(handleWSMessage);

  // Subscribe to price channel when symbol changes
  useEffect(() => {
    const channel = `price:${symbol}`;
    wsSub(channel);
    return () => wsUnsub(channel);
  }, [symbol, wsSub, wsUnsub]);

  // Subscribe to account channel
  useEffect(() => {
    wsSub("account");
    return () => wsUnsub("account");
  }, [wsSub, wsUnsub]);

  const backendAlive = useRef(true);
  const warnedRef = useRef(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.get("/api/broker/status");
      setBroker(res.data);
      if (!backendAlive.current) {
        backendAlive.current = true;
        warnedRef.current = false;
        // Backend recovered — immediately refetch data and candles
        fetchData();
        fetchCandles();
      }
    } catch {
      if (!warnedRef.current) {
        debugWarn("Backend unreachable — polling paused until it recovers");
        warnedRef.current = true;
      }
      backendAlive.current = false;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchData = useCallback(async () => {
    if (fetchingRef.current || !backendAlive.current) return;
    fetchingRef.current = true;
    try {
      const [acct, pos, ord, tr, logs, pnl] = await Promise.all([
        api.get("/api/broker/account"),
        api.get("/api/broker/positions"),
        api.get("/api/broker/orders"),
        api.get("/api/agents/all-trades?limit=100"),
        api.get("/api/agents/engine-logs?limit=100"),
        api.get("/api/agents/pnl-summary"),
      ]);
      setAccount(acct.data);
      setPositions(pos.data);
      setOrders(ord.data);
      setTrades(tr.data);
      setEngineLogs(logs.data);
      setPnlSummary(pnl.data);
    } catch {
      backendAlive.current = false;
    } finally {
      setLoading(false);
      fetchingRef.current = false;
    }
  }, []);

  const fetchCandles = useCallback(async () => {
    if (!backendAlive.current) return;
    try {
      const res = await api.get(`/api/broker/candles/${symbol}?timeframe=${timeframe}&count=200&source=${dataSource}`);
      setCandles(res.data);
    } catch {
      // silent — backendAlive handles retry
    }
  }, [symbol, timeframe, dataSource]);

  // Clear candles when symbol/timeframe/dataSource changes and immediately fetch new data
  useEffect(() => {
    setCandles([]);
    setTicks([]);
    fetchCandles();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, timeframe, dataSource]);

  // Fetch tick data when tick view is active
  const fetchTicks = useCallback(async () => {
    if (!showTicks || dataSource !== "databento") return;
    try {
      const res = await api.get(`/api/broker/ticks/${symbol}?count=500&seconds_back=300`);
      if (Array.isArray(res.data)) setTicks(res.data);
    } catch {}
  }, [symbol, showTicks, dataSource]);

  // Fetch available data sources
  useEffect(() => {
    api.get("/api/market-data/sources").then((r) => setDataSources(r.data)).catch((e) => debugWarn("fetch failed:", e?.message));
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);
  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => { fetchCandles(); }, [fetchCandles]);

  useEffect(() => { fetchTicks(); }, [fetchTicks]);

  // Polling with cleanup — also retries status check to recover when backend comes back
  // When backend is alive: poll data every 5s
  // When backend is dead: retry status every 30s to recover
  const recoveryRef = useRef<ReturnType<typeof setInterval>>(undefined);
  useEffect(() => {
    pollRef.current = setInterval(() => {
      if (backendAlive.current) {
        fetchData();
        fetchCandles();
        fetchTicks();
      }
    }, 5000);
    // Separate slower recovery interval for when backend is down
    recoveryRef.current = setInterval(() => {
      if (!backendAlive.current) {
        fetchStatus();
      }
    }, 30000);
    // Also run an initial status check when backend is down
    if (!backendAlive.current) fetchStatus();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (recoveryRef.current) clearInterval(recoveryRef.current);
    };
  }, [fetchData, fetchCandles, fetchStatus, fetchTicks]);

  const handleClosePosition = async (id: string) => {
    try {
      const res = await api.post(`/api/broker/close/${id}`);
      if (res.data.success) {
        toast.success(`Position closed — P&L: ${fmt(res.data.pnl)}`);
      } else {
        toast.error(res.data.message || "Failed to close position");
      }
      fetchData();
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
    setConfirmClose(null);
  };

  const activeAgents = pnlSummary.length;

  // ── Column definitions ──────────────────────────────────────────

  const positionCols: Column<LivePosition>[] = [
    { header: "Symbol", key: "symbol" },
    { header: "Side", key: "direction", render: (r) => <StatusBadge value={r.direction} /> },
    { header: "Size", key: "size", align: "right" },
    { header: "Entry", key: "entry_price", align: "right", render: (r) => fmt(r.entry_price) },
    { header: "Current", key: "current_price", align: "right", render: (r) => fmt(r.current_price) },
    { header: "SL", key: "sl", align: "right", render: (r) => r.sl ? <span className="text-red-400">{fmt(r.sl)}</span> : <span style={{ color: "var(--muted)" }}>{"\u2014"}</span> },
    { header: "TP", key: "tp", align: "right", render: (r) => r.tp ? <span className="text-emerald-400">{fmt(r.tp)}</span> : <span style={{ color: "var(--muted)" }}>{"\u2014"}</span> },
    { header: "P&L", key: "pnl", align: "right", render: (r) => (
      <span className={r.pnl >= 0 ? "text-emerald-400" : "text-red-400"}>{r.pnl >= 0 ? "+" : ""}{fmt(r.pnl)}</span>
    )},
    { header: "", key: "action", render: (r) => (
      <button onClick={() => setConfirmClose(r.id)} className="text-xs px-2 py-1 rounded border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>Close</button>
    )},
  ];

  const orderCols: Column<LiveOrder>[] = [
    { header: "Symbol", key: "symbol" },
    { header: "Side", key: "direction", render: (r) => <StatusBadge value={r.direction} /> },
    { header: "Type", key: "order_type" },
    { header: "Size", key: "size", align: "right" },
    { header: "Price", key: "price", align: "right", render: (r) => fmt(r.price) },
    { header: "SL", key: "sl", align: "right", render: (r) => r.sl ? <span className="text-red-400">{fmt(r.sl)}</span> : <span style={{ color: "var(--muted)" }}>{"\u2014"}</span> },
    { header: "TP", key: "tp", align: "right", render: (r) => r.tp ? <span className="text-emerald-400">{fmt(r.tp)}</span> : <span style={{ color: "var(--muted)" }}>{"\u2014"}</span> },
    { header: "Status", key: "status", render: (r) => <StatusBadge value={r.status} /> },
  ];

  const tradeCols: Column<AgentTrade>[] = [
    { header: "Agent", key: "agent_name" as keyof AgentTrade, render: (r) => {
      const row = r as unknown as { agent_name?: string; agent_type?: string };
      const name = row.agent_name || "—";
      const type = row.agent_type || "";
      return (
        <div className="flex flex-col max-w-[130px]">
          <span className="text-xs font-medium truncate" title={name}>{name}</span>
          {type && <span className="text-[10px] truncate" style={{ color: "var(--muted)" }}>{type}</span>}
        </div>
      );
    }},
    { header: "Symbol", key: "symbol" },
    { header: "Side", key: "direction", render: (r) => <StatusBadge value={r.direction} /> },
    { header: "Size", key: "lot_size", align: "right" },
    { header: "Entry", key: "entry_price", align: "right", render: (r) => fmt(r.entry_price) },
    { header: "SL", key: "stop_loss", align: "right", render: (r) => r.stop_loss !== null && r.stop_loss !== undefined ? <span className="text-red-400">{fmt(r.stop_loss)}</span> : <span style={{ color: "var(--muted)" }}>{"\u2014"}</span> },
    { header: "TP", key: "take_profit", align: "right", render: (r) => r.take_profit !== null && r.take_profit !== undefined ? <span className="text-emerald-400">{fmt(r.take_profit)}</span> : <span style={{ color: "var(--muted)" }}>{"\u2014"}</span> },
    { header: "Exit", key: "exit_price", align: "right", render: (r) => r.exit_price ? fmt(r.exit_price) : "\u2014" },
    { header: "P&L", key: "pnl", align: "right", render: (r) => {
      const p = r.broker_pnl ?? r.pnl ?? 0;
      return <span className={p >= 0 ? "text-emerald-400" : "text-red-400"}>{p >= 0 ? "+" : ""}{fmt(p)}</span>;
    }},
    { header: "Reason", key: "exit_reason", render: (r) => r.exit_reason ? <StatusBadge value={r.exit_reason} /> : <span style={{ color: "var(--muted)" }}>{"\u2014"}</span> },
    { header: "Status", key: "status", render: (r) => <StatusBadge value={r.status} /> },
  ];

  return (
    <div className="space-y-4">
      {/* ── Top Bar ────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3">
        <SearchableSelect options={SYMBOLS} value={symbol} onChange={(v) => { setSymbol(v); setIndicatorMenuOpen(false); }} className="w-40" />

        {/* Data Source Selector */}
        {dataSources.length > 1 && (
          <div className="flex items-center gap-1">
            {dataSources.map((src) => {
              const isSupported = src.symbols === "*" || (Array.isArray(src.symbols) && src.symbols.includes(symbol));
              return (
                <button
                  key={src.name}
                  onClick={() => isSupported && setDataSource(src.name)}
                  disabled={!isSupported}
                  className={`flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                    dataSource === src.name ? "bg-purple-600 text-white" : "hover:bg-white/10 disabled:opacity-30"
                  }`}
                  style={{ color: dataSource === src.name ? undefined : "var(--muted)" }}
                  title={!isSupported ? `${src.label} doesn't support ${symbol}` : src.label}
                >
                  <Database size={12} />
                  {src.label}
                </button>
              );
            })}
            {/* Tick data toggle — only for Databento */}
            {dataSource === "databento" && dataSources.find(s => s.name === "databento")?.has_ticks && (
              <button
                onClick={() => setShowTicks(!showTicks)}
                className={`px-2.5 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                  showTicks ? "bg-amber-600 text-white" : "hover:bg-white/10"
                }`}
                style={{ color: showTicks ? undefined : "var(--muted)" }}
              >
                Ticks
              </button>
            )}
          </div>
        )}

        {/* Indicator Toggle */}
        <div className="relative">
          <button onClick={() => setIndicatorMenuOpen(!indicatorMenuOpen)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg border hover:bg-white/5"
            style={{ borderColor: "var(--border)", color: Object.values(indicators).some(Boolean) ? "var(--accent)" : "var(--muted)" }}>
            <SlidersHorizontal size={14} /> Indicators
          </button>
          {indicatorMenuOpen && (
            <div className="absolute top-full left-0 mt-1 w-48 rounded-lg border shadow-xl z-50 p-2 space-y-1"
              style={{ background: "var(--card)", borderColor: "var(--border)" }}>
              {([["ema8", "EMA 8"], ["ema21", "EMA 21"], ["ema50", "EMA 50"], ["sma200", "SMA 200"], ["bollinger", "Bollinger Bands"], ["rsi14", "RSI (14)"]] as const).map(([key, label]) => (
                <label key={key} className="flex items-center gap-2 px-2 py-1.5 text-xs rounded hover:bg-white/5 cursor-pointer">
                  <input type="checkbox" checked={!!indicators[key]} onChange={() => toggleIndicator(key)} className="rounded" />
                  {label}
                </label>
              ))}
            </div>
          )}
        </div>

        <div className="flex gap-1">
          {(dataSource === "databento" ? DATABENTO_TIMEFRAMES : BROKER_TIMEFRAMES).map((tf) => (
            <button key={tf} onClick={() => { setTimeframe(tf); setIndicatorMenuOpen(false); }}
              className={`px-2.5 py-1.5 text-xs font-medium rounded transition-colors ${
                timeframe === tf ? "bg-blue-600 text-white" : "hover:bg-white/10"
              }`}
              style={{ color: timeframe === tf ? undefined : "var(--muted)" }}
            >{tf}</button>
          ))}
        </div>

        <div className="ml-auto flex gap-2">
          {!broker.connected && (
            <button onClick={() => setBrokerModal(true)} className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>
              <Plug size={14} /> Connect Broker
            </button>
          )}
          {broker.connected && (
            <span className="flex items-center gap-1.5 px-3 py-2 text-xs rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" /> {broker.broker}
            </span>
          )}
          <WSStatusBadge status={wsStatus} />
          <button onClick={() => setOrderOpen(true)} className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white">
            <ShoppingCart size={14} /> Order
          </button>
          <button onClick={() => setWizardOpen(true)} className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>
            <Plus size={14} /> Agent
          </button>
        </div>
      </div>

      {/* ── Chart ──────────────────────────────────────────────────── */}
      <Glass padding="none" className="overflow-hidden">
        <div className="px-4 pt-3 pb-1 flex items-center gap-3 md:gap-4 flex-wrap">
          <span className="text-sm font-semibold">{symbol}</span>
          {(liveBid || candles.length > 0) && (
            <>
              <span className="text-xl md:text-2xl font-semibold tabular" style={{ letterSpacing: "-0.02em" }}>
                {liveBid ? fmt(liveBid) : candles.length > 0 ? fmt(candles[candles.length - 1].close) : "—"}
              </span>
              {liveBid && liveAsk && (
                <span className="text-xs tabular" style={{ color: "var(--muted)" }}>
                  Bid <span className="text-emerald-400">{fmt(liveBid)}</span>
                  {" · "}Ask <span className="text-red-400">{fmt(liveAsk)}</span>
                  {" · "}Spread {((liveAsk - liveBid) * 10).toFixed(1)}
                </span>
              )}
              <span className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
                {timeframe}
              </span>
              {lastTickTime > 0 && Date.now() - lastTickTime < 10000 && (
                <span className="flex items-center gap-1 text-[10px]" style={{ color: "var(--status-live)" }}>
                  <span className="pulse-dot" style={{ background: "var(--status-live)" }} />
                  live
                </span>
              )}
            </>
          )}
        </div>
        {candles.length > 0 ? (
          <CandlestickChart candles={candles} height={380} indicators={indicators} markers={chartMarkers} />
        ) : (
          <div className="flex items-center justify-center h-[380px] text-sm" style={{ color: "var(--muted)" }}>
            {broker.connected || dataSource === "databento" ? "Loading chart..." : "Connect a broker or select Databento to load chart data"}
          </div>
        )}
      </Glass>

      {/* ── Tick Data Panel ─────────────────────────────────────────── */}
      {showTicks && ticks.length > 0 && (
        <Glass padding="md">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-medium">Tick Data — {symbol} (last 5 min, {ticks.length} ticks)</h3>
            <span className="text-xs px-2 py-0.5 rounded" style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>Databento</span>
          </div>
          <div className="max-h-48 overflow-y-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ color: "var(--muted)" }}>
                  <th className="text-left py-1 px-2">Time</th>
                  <th className="text-right py-1 px-2">Price</th>
                  <th className="text-right py-1 px-2">Size</th>
                  <th className="text-center py-1 px-2">Side</th>
                </tr>
              </thead>
              <tbody>
                {ticks.slice(-100).reverse().map((t, i) => (
                  <tr key={i} className="border-t" style={{ borderColor: "var(--border)" }}>
                    <td className="py-0.5 px-2" style={{ color: "var(--muted)" }}>
                      {toSydneyTime(t.time)}
                    </td>
                    <td className="text-right py-0.5 px-2 font-mono">{t.price.toFixed(2)}</td>
                    <td className="text-right py-0.5 px-2">{t.size}</td>
                    <td className="text-center py-0.5 px-2">
                      <span className={t.side === "buy" ? "text-emerald-400" : t.side === "sell" ? "text-red-400" : ""}>
                        {t.side || "—"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Glass>
      )}

      {/* ── Account Cards ──────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-2 md:gap-3">
        <TradingTile label="Balance" value={account ? fmt(account.balance) : "—"} sub={account?.currency} />
        <TradingTile label="Equity" value={account ? fmt(account.equity) : "—"} />
        <TradingTile
          label="Unreal. P&L"
          value={account ? fmt(account.unrealized_pnl) : "—"}
          color={account && account.unrealized_pnl >= 0 ? "up" : account && account.unrealized_pnl < 0 ? "down" : undefined}
        />
        <TradingTile
          label="Margin Used"
          value={account ? fmt(account.margin_used) : "—"}
          sub={account && account.balance > 0 ? `${((account.margin_used / account.balance) * 100).toFixed(1)}%` : undefined}
          icon={<Gauge size={12} />}
        />
        <TradingTile label="Positions" value={positions.length} sub={orders.length > 0 ? `${orders.length} orders` : undefined} />
        <TradingTile label="Active Agents" value={activeAgents} icon={<Activity size={12} />} />
      </div>

      {/* ── Tabs ───────────────────────────────────────────────────── */}
      <Glass padding="md">
        <Tabs tabs={[
          {
            label: "Agents",
            content: <AgentPanel onRefresh={fetchData} />,
          },
          {
            label: "Positions",
            badge: positions.length,
            content: <DataTable columns={positionCols as unknown as Column<Record<string, unknown>>[]} data={positions as unknown as Record<string, unknown>[]} emptyMessage="No open positions" />,
          },
          {
            label: "Orders",
            badge: orders.length,
            content: <DataTable columns={orderCols as unknown as Column<Record<string, unknown>>[]} data={orders as unknown as Record<string, unknown>[]} emptyMessage="No pending orders" />,
          },
          {
            label: "History",
            content: (
              <div>
                {historyStats.count > 0 && (
                  <div className="flex flex-wrap gap-4 mb-3 text-xs" style={{ color: "var(--muted)" }}>
                    <span>Total P&L: <span className={(historyStats.total >= 0 ? "text-emerald-400" : "text-red-400") + " font-medium"}>{fmt(historyStats.total)}</span></span>
                    <span>Win Rate: <span className="text-white font-medium">{historyStats.winRate.toFixed(1)}%</span></span>
                    <span>Avg Win: <span className="text-emerald-400">{fmt(historyStats.avgWin)}</span></span>
                    <span>Avg Loss: <span className="text-red-400">{fmt(historyStats.avgLoss)}</span></span>
                    <span>Trades: <span className="text-white">{historyStats.count}</span></span>
                  </div>
                )}
                <DataTable columns={tradeCols as unknown as Column<Record<string, unknown>>[]} data={trades as unknown as Record<string, unknown>[]} emptyMessage="No trade history" paginated pageSize={25} />
              </div>
            ),
          },
          {
            label: "Engine Log",
            badge: filteredLogs.length,
            content: (
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <select value={logFilter} onChange={(e) => setLogFilter(e.target.value)}
                    className="px-2 py-1 text-xs rounded border bg-transparent" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                    <option value="all">All Levels</option>
                    <option value="info">Info</option>
                    <option value="warn">Warn</option>
                    <option value="error">Error</option>
                    <option value="signal">Signal</option>
                    <option value="trade">Trade</option>
                  </select>
                  <input value={logSearch} onChange={(e) => setLogSearch(e.target.value)} placeholder="Search logs..."
                    className="flex-1 px-2 py-1 text-xs rounded border bg-transparent outline-none" style={{ borderColor: "var(--border)" }} />
                  {(logFilter !== "all" || logSearch) && (
                    <button onClick={() => { setLogFilter("all"); setLogSearch(""); }} className="text-xs text-blue-400 hover:text-blue-300">Clear</button>
                  )}
                </div>
                <div className="max-h-80 overflow-y-auto text-xs font-mono space-y-0.5">
                  {filteredLogs.length === 0 ? (
                    <p className="py-4 text-center text-sm" style={{ color: "var(--muted)" }}>No matching logs</p>
                  ) : (
                    filteredLogs.map((l) => (
                      <div key={l.id} className="flex items-start gap-2 py-1">
                        <span className="flex-shrink-0" style={{ color: "var(--muted)" }}>
                          {toSydneyTime(l.created_at + (l.created_at.includes("Z") || l.created_at.includes("+") ? "" : "Z"))}
                        </span>
                        <StatusBadge value={l.level} />
                        <span className="break-all">{l.message}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            ),
          },
        ]} />
      </Glass>

      {/* ── Modals ─────────────────────────────────────────────────── */}
      <BrokerModal open={brokerModal} onClose={() => setBrokerModal(false)} onConnected={() => { fetchStatus(); fetchData(); fetchCandles(); }} />
      <AgentWizard open={wizardOpen} onClose={() => setWizardOpen(false)} onCreated={fetchData} />
      <OrderPanel key={symbol} open={orderOpen} onClose={() => setOrderOpen(false)} defaultSymbol={symbol} />
      <ConfirmDialog
        open={confirmClose !== null}
        onClose={() => setConfirmClose(null)}
        onConfirm={() => confirmClose && handleClosePosition(confirmClose)}
        title="Close Position"
        message="Close this position? This action cannot be undone."
        confirmLabel="Close Position"
        variant="danger"
      />
    </div>
  );
}

// ── TradingTile — compact stat card aligned with dashboard style ──────
function TradingTile({
  label, value, sub, color, icon,
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: "up" | "down";
  icon?: React.ReactNode;
}) {
  const valueClass = color === "up" ? "text-emerald-400" : color === "down" ? "text-red-400" : "";
  return (
    <Glass padding="sm" className="flex flex-col justify-between min-h-[70px]">
      <div className="flex items-center justify-between">
        <span className="text-[9px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
          {label}
        </span>
        {icon && <span style={{ color: "var(--muted)" }}>{icon}</span>}
      </div>
      <div>
        <div className={`text-base md:text-lg font-semibold tabular ${valueClass}`}
             style={{ letterSpacing: "-0.01em" }}>
          {value}
        </div>
        {sub && (
          <div className="text-[10px] mt-0.5 tabular" style={{ color: "var(--muted)" }}>
            {sub}
          </div>
        )}
      </div>
    </Glass>
  );
}
