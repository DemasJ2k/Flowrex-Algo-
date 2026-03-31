// ── Account & Broker ──────────────────────────────────────────────────

export interface AccountInfo {
  balance: number;
  equity: number;
  margin_used: number;
  unrealized_pnl: number;
  currency: string;
}

export interface LivePosition {
  id: string;
  symbol: string;
  direction: string;
  size: number;
  entry_price: number;
  current_price: number;
  pnl: number;
  sl?: number | null;
  tp?: number | null;
}

export interface LiveOrder {
  id: string;
  symbol: string;
  direction: string;
  size: number;
  order_type: string;
  price: number;
  status: string;
  sl?: number | null;
  tp?: number | null;
}

export interface BrokerStatus {
  connected: boolean;
  broker: string | null;
}

export interface PlaceOrderRequest {
  symbol: string;
  direction: string;
  size: number;
  order_type: string;
  price?: number | null;
  sl?: number | null;
  tp?: number | null;
  broker?: string | null;
}

export interface PlaceOrderResponse {
  success: boolean;
  order_id: string;
  message: string;
}

export interface BrokerConnectRequest {
  broker_name: string;
  credentials: Record<string, string | number | boolean>;
}

// ── Candles ───────────────────────────────────────────────────────────

export interface CandleData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// ── Agents ────────────────────────────────────────────────────────────

export interface Agent {
  id: number;
  name: string;
  symbol: string;
  timeframe: string;
  agent_type: string;
  broker_name: string;
  mode: string;
  status: string;
  risk_config: Record<string, number>;
  created_at: string;
  deleted_at?: string | null;
  trade_count: number;
  total_pnl: number;
}

export interface AgentLog {
  id: number;
  agent_id: number;
  level: string;
  message: string;
  data?: Record<string, unknown> | null;
  created_at: string;
}

export interface AgentTrade {
  id: number;
  agent_id: number;
  symbol: string;
  direction: string;
  entry_price: number;
  exit_price?: number | null;
  stop_loss: number;
  take_profit: number;
  lot_size: number;
  pnl?: number | null;
  broker_pnl?: number | null;
  broker_ticket?: string | null;
  status: string;
  exit_reason?: string | null;
  confidence?: number | null;
  signal_data?: Record<string, unknown> | null;
  entry_time: string;
  exit_time?: string | null;
}

export interface EngineLog {
  id: number;
  agent_id: number;
  level: string;
  message: string;
  data?: Record<string, unknown> | null;
  created_at: string;
}

export interface PnlSummaryItem {
  agent_id: number;
  agent_name: string;
  symbol: string;
  total_pnl: number;
  trade_count: number;
  win_count: number;
  loss_count: number;
}

export interface EquityCurvePoint {
  time: string;
  pnl: number;
}

export interface AgentPerformance {
  total_trades: number;
  open_trades: number;
  closed_trades: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl_per_trade: number;
  avg_win: number;
  avg_loss: number;
  best_trade: number;
  worst_trade: number;
  profit_factor: number;
  sharpe_ratio: number;
  max_drawdown: number;
  max_win_streak: number;
  max_loss_streak: number;
  equity_curve: EquityCurvePoint[];
}

// ── ML Models ─────────────────────────────────────────────────────────

export interface MLModel {
  id: number;
  symbol: string;
  timeframe: string;
  model_type: string;
  pipeline: string;
  grade?: string | null;
  metrics: Record<string, number>;
  trained_at: string;
}

// ── Settings ──────────────────────────────────────────────────────────

export interface UserSettings {
  theme: string;
  default_broker?: string | null;
  notifications_enabled: boolean;
  settings_json: Record<string, unknown>;
}

// ── Symbol Info ───────────────────────────────────────────────────────

export interface SymbolInfo {
  name: string;
  min_lot: number;
  lot_step: number;
  pip_size: number;
  digits: number;
}
