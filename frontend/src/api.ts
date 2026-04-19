// ── Types ──────────────────────────────────────────────────────────────────────

export type Bias = "BUY" | "WATCH" | "AVOID";
export type SentimentLabel = "positive" | "neutral" | "negative";
export type SpyTrend = "bullish" | "neutral" | "bearish";

export interface TradeLevels {
  entry_low: number | null;
  entry_high: number | null;
  stop_loss: number | null;
  target_1: number | null;
  target_2: number | null;
  risk_reward: number | null;
}

export interface TechnicalSnapshot {
  price: number | null;
  rsi_14: number | null;
  bb_pct: number | null;
  ma20: number | null;
  ma50: number | null;
  ma200: number | null;
  atr: number | null;
  zscore: number | null;
}

export interface NewsItem {
  title: string;
  url?: string;
  source?: string;
  published_at?: string;
  sentiment?: string;
}

export interface Signal {
  id: string;
  symbol: string;
  ts: string;
  name?: string;
  sector?: string;

  // Scores
  dip_score: number | null;
  sentiment_score: number | null;
  composite_score: number | null;

  // Decision
  bias: Bias | null;
  confidence: number | null;
  reasoning: string | null;

  // Sub-scores
  rsi_score?: number | null;
  bb_score?: number | null;
  ma_deviation_score?: number | null;
  volume_score?: number | null;
  zscore_score?: number | null;
  mean_rev_score?: number | null;
  support_score?: number | null;

  // Context
  sentiment_label?: SentimentLabel | null;
  news_type?: string | null;
  spy_trend?: SpyTrend | null;
  sector_rs?: number | null;
  vix_level?: number | null;

  // Nested
  levels?: TradeLevels | null;
  technicals?: TechnicalSnapshot | null;
  news?: NewsItem[] | null;
}

export interface SignalListResponse {
  signals: Signal[];
  total: number;
  page: number;
  page_size: number;
}

export interface WatchlistEntry {
  id: string;
  name: string;
  symbol: string;
  added_at: string;
  notes?: string;
}

export interface MarketContext {
  spy_trend: SpyTrend;
  spy_rsi: number;
  spy_vs_ma200_pct: number;
  vix_level: number;
  regime: string;
  vix_regime: string;
}

export interface BacktestResult {
  id: string;
  run_at: string;
  total_trades: number;
  win_rate: number;
  avg_return: number;
  max_drawdown: number;
  profit_factor: number;
  sharpe_ratio: number;
  equity_curve: { trade: number; equity: number }[];
  trade_log: {
    symbol: string;
    entry_date: string;
    exit_date: string;
    entry_price: number;
    exit_price: number;
    return_pct: number;
    exit_reason: string;
  }[];
}

// ── API Client ─────────────────────────────────────────────────────────────────

import axios from "axios";

const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export const api = axios.create({ baseURL: BASE });

export const signalsApi = {
  list: (params: {
    bias?: string;
    min_dip_score?: number;
    min_confidence?: number;
    sentiment?: string;
    sort_by?: string;
    page?: number;
    page_size?: number;
  }) => api.get<SignalListResponse>("/api/signals", { params }),

  get: (id: string) => api.get<Signal>(`/api/signals/${id}`),
};

export const watchlistApi = {
  list: () => api.get<WatchlistEntry[]>("/api/watchlist"),
  add: (symbol: string, name?: string, notes?: string) =>
    api.post<WatchlistEntry>("/api/watchlist", { symbol, name: name || "Default", notes }),
  remove: (id: string) => api.delete(`/api/watchlist/${id}`),
};

export const marketApi = {
  context: () => api.get<MarketContext>("/api/market/context"),
  triggerScan: () => api.post("/api/scan/trigger"),
};

export const backtestApi = {
  run: (payload: object) => api.post<BacktestResult>("/api/backtest", payload),
  get: (id: string) => api.get<BacktestResult>(`/api/backtest/${id}`),
};

// ── WebSocket hook ─────────────────────────────────────────────────────────────

export function createWsConnection(
  onMessage: (msg: { type: string; payload: unknown }) => void
): WebSocket {
  const wsBase = (import.meta.env.VITE_WS_URL || "ws://localhost:8000");
  const ws = new WebSocket(`${wsBase}/ws`);

  ws.onopen = () => {
    console.log("[WS] connected");
    // Start keep-alive ping every 20s
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      else clearInterval(ping);
    }, 20_000);
  };

  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      onMessage(msg);
    } catch {
      // ignore malformed frames
    }
  };

  ws.onclose = () => console.log("[WS] disconnected");
  return ws;
}
