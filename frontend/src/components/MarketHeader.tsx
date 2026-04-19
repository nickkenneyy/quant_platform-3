// ── MarketHeader ───────────────────────────────────────────────────────────────

import { useEffect } from "react";
import { useStore } from "../store";

export function MarketHeader() {
  const { marketContext, fetchMarketContext } = useStore();

  useEffect(() => {
    // Refresh every 5 minutes
    const id = setInterval(fetchMarketContext, 300_000);
    return () => clearInterval(id);
  }, []);

  if (!marketContext) {
    return <div className="h-10 border-b border-gray-800 bg-gray-950" />;
  }

  const m = marketContext;
  const trendColor =
    m.spy_trend === "bullish" ? "text-emerald-400" :
    m.spy_trend === "bearish" ? "text-red-400" : "text-gray-400";

  const vixColor =
    m.vix_level > 30 ? "text-red-400" :
    m.vix_level > 20 ? "text-amber-400" : "text-emerald-400";

  return (
    <div className="border-b border-gray-800 bg-gray-950">
      <div className="max-w-screen-2xl mx-auto px-4 py-2 flex items-center gap-8 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-gray-600">SPY</span>
          <span className={`font-semibold ${trendColor}`}>{m.spy_trend.toUpperCase()}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-gray-600">vs 200MA</span>
          <span className={m.spy_vs_ma200_pct >= 0 ? "text-emerald-400" : "text-red-400"}>
            {m.spy_vs_ma200_pct >= 0 ? "+" : ""}{m.spy_vs_ma200_pct.toFixed(1)}%
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-gray-600">VIX</span>
          <span className={`font-semibold ${vixColor}`}>{m.vix_level.toFixed(1)}</span>
          <span className="text-gray-600">({m.vix_regime})</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-gray-600">REGIME</span>
          <span className={
            m.regime === "bullish" ? "text-emerald-400" :
            m.regime === "bearish" ? "text-red-400" : "text-gray-400"
          }>
            {m.regime.toUpperCase()}
          </span>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-gray-600">LIVE</span>
        </div>
      </div>
    </div>
  );
}


// ── FilterBar ──────────────────────────────────────────────────────────────────

export function FilterBar() {
  const { signalFilters, setFilter } = useStore();

  return (
    <div className="flex flex-wrap items-center gap-3 mb-4">
      {/* Bias filter */}
      <select
        value={signalFilters.bias ?? ""}
        onChange={(e) => setFilter("bias", e.target.value || undefined)}
        className="bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-300
                   focus:border-emerald-500 focus:outline-none"
      >
        <option value="">All signals</option>
        <option value="BUY">BUY only</option>
        <option value="WATCH">WATCH only</option>
        <option value="AVOID">AVOID only</option>
      </select>

      {/* Min dip score */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-500">Min dip</span>
        <input
          type="number"
          min={0}
          max={100}
          step={5}
          value={signalFilters.min_dip_score}
          onChange={(e) => setFilter("min_dip_score", Number(e.target.value))}
          className="w-16 bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs
                     text-gray-300 focus:border-emerald-500 focus:outline-none"
        />
      </div>

      {/* Min confidence */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-500">Min conf</span>
        <input
          type="number"
          min={0}
          max={100}
          step={5}
          value={signalFilters.min_confidence}
          onChange={(e) => setFilter("min_confidence", Number(e.target.value))}
          className="w-16 bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs
                     text-gray-300 focus:border-emerald-500 focus:outline-none"
        />
      </div>

      {/* Sentiment filter */}
      <select
        value={(signalFilters as Record<string, unknown>).sentiment as string ?? ""}
        onChange={(e) => setFilter("sentiment", e.target.value || undefined)}
        className="bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-300
                   focus:border-emerald-500 focus:outline-none"
      >
        <option value="">Any sentiment</option>
        <option value="positive">Positive</option>
        <option value="neutral">Neutral</option>
        <option value="negative">Negative</option>
      </select>

      {/* Sort */}
      <select
        value={signalFilters.sort_by}
        onChange={(e) => setFilter("sort_by", e.target.value)}
        className="bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-300
                   focus:border-emerald-500 focus:outline-none"
      >
        <option value="composite_score">Sort: Composite</option>
        <option value="dip_score">Sort: Dip score</option>
        <option value="confidence">Sort: Confidence</option>
        <option value="sentiment_score">Sort: Sentiment</option>
        <option value="ts">Sort: Recent</option>
      </select>
    </div>
  );
}


// ── WatchlistPanel ─────────────────────────────────────────────────────────────

export function WatchlistPanel() {
  const { watchlist, watchlistLoading, removeFromWatchlist, fetchWatchlist, fetchSignals } = useStore();

  useEffect(() => { fetchWatchlist(); }, []);

  return (
    <div className="max-w-lg">
      <h2 className="text-sm font-semibold text-gray-300 mb-6 tracking-wider uppercase">
        Watchlist
      </h2>

      {watchlistLoading ? (
        <div className="text-gray-600 text-sm animate-pulse">Loading…</div>
      ) : watchlist.length === 0 ? (
        <div className="text-gray-600 text-sm">
          No tickers watched. Click "+ Watch" on any signal to add it here.
        </div>
      ) : (
        <div className="space-y-2">
          {watchlist.map((w) => (
            <div
              key={w.id}
              className="flex items-center justify-between px-4 py-3 bg-gray-900
                         border border-gray-800 rounded"
            >
              <div>
                <div className="text-sm font-semibold text-gray-200">{w.symbol}</div>
                <div className="text-xs text-gray-500">
                  Added {new Date(w.added_at).toLocaleDateString()}
                  {w.notes && ` · ${w.notes}`}
                </div>
              </div>
              <button
                onClick={() => removeFromWatchlist(w.id)}
                className="text-xs text-gray-600 hover:text-red-400 transition-colors"
              >
                remove
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
