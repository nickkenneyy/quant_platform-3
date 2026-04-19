import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import { createWsConnection } from "../api";
import type { Signal } from "../api";
import { SignalCard } from "./SignalCard";
import { SignalDetail } from "./SignalDetail";
import { MarketHeader } from "./MarketHeader";
import { FilterBar } from "./FilterBar";
import { WatchlistPanel } from "./WatchlistPanel";

export function Dashboard() {
  const {
    signals, totalSignals, signalsLoading,
    fetchSignals, fetchWatchlist, fetchMarketContext,
    handleWsMessage, lastScanAt, scanRunning, triggerScan,
    selectedSignal, selectSignal,
  } = useStore();

  const wsRef = useRef<WebSocket | null>(null);
  const [activeTab, setActiveTab] = useState<"signals" | "watchlist" | "backtest">("signals");

  // Initial load
  useEffect(() => {
    fetchSignals();
    fetchWatchlist();
    fetchMarketContext();
  }, []);

  // WebSocket
  useEffect(() => {
    wsRef.current = createWsConnection(handleWsMessage);
    return () => wsRef.current?.close();
  }, []);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 font-mono">
      {/* Top bar */}
      <header className="border-b border-gray-800 bg-gray-950 sticky top-0 z-50">
        <div className="max-w-screen-2xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-7 h-7 rounded bg-emerald-500 flex items-center justify-center">
              <span className="text-xs font-bold text-gray-950">QD</span>
            </div>
            <span className="text-sm font-semibold tracking-wider text-gray-200">
              QUANT DIP FINDER
            </span>
            <span className="text-xs text-gray-500 ml-2">v1.0</span>
          </div>

          <div className="flex items-center gap-4">
            {lastScanAt && (
              <span className="text-xs text-gray-500">
                Last scan: {new Date(lastScanAt).toLocaleTimeString()}
              </span>
            )}
            <button
              onClick={triggerScan}
              disabled={scanRunning}
              className="text-xs px-3 py-1.5 rounded border border-gray-700 hover:border-emerald-500
                         hover:text-emerald-400 transition-colors disabled:opacity-40"
            >
              {scanRunning ? "SCANNING…" : "⟳ SCAN NOW"}
            </button>
          </div>
        </div>
      </header>

      {/* Market context bar */}
      <MarketHeader />

      {/* Tab nav */}
      <div className="border-b border-gray-800">
        <div className="max-w-screen-2xl mx-auto px-4 flex gap-6">
          {(["signals", "watchlist", "backtest"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`text-xs py-3 uppercase tracking-widest border-b-2 transition-colors ${
                activeTab === tab
                  ? "border-emerald-500 text-emerald-400"
                  : "border-transparent text-gray-500 hover:text-gray-300"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>
      </div>

      {/* Main content */}
      <div className="max-w-screen-2xl mx-auto px-4 py-6">
        {activeTab === "signals" && (
          <div className="flex gap-6">
            {/* Left: Signal list */}
            <div className="flex-1 min-w-0">
              <FilterBar />
              <div className="mt-1 text-xs text-gray-500 mb-4">
                {totalSignals} signals · page results sorted by composite score
              </div>
              {signalsLoading ? (
                <div className="text-center py-20 text-gray-600 text-sm animate-pulse">
                  Running quantitative scan…
                </div>
              ) : signals.length === 0 ? (
                <div className="text-center py-20 text-gray-600 text-sm">
                  No signals match current filters.
                </div>
              ) : (
                <div className="space-y-2">
                  {signals.map((sig) => (
                    <SignalCard
                      key={sig.id}
                      signal={sig}
                      isSelected={selectedSignal?.id === sig.id}
                      onClick={() => selectSignal(selectedSignal?.id === sig.id ? null : sig)}
                    />
                  ))}
                </div>
              )}
            </div>

            {/* Right: Detail panel */}
            {selectedSignal && (
              <div className="w-96 shrink-0">
                <SignalDetail signal={selectedSignal} onClose={() => selectSignal(null)} />
              </div>
            )}
          </div>
        )}

        {activeTab === "watchlist" && <WatchlistPanel />}
        {activeTab === "backtest" && <BacktestView />}
      </div>
    </div>
  );
}

// ── Inline backtest view (simple) ──────────────────────────────────────────────

function BacktestView() {
  const [params, setParams] = useState({
    start_date: "2022-01-01",
    end_date: "2023-12-31",
    universe: "SP500",
    dip_score_threshold: 60,
    hold_days: 5,
  });
  const [result, setResult] = useState<import("../api").BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    setLoading(true);
    try {
      const { backtestApi } = await import("../api");
      const { data } = await backtestApi.run(params);
      setResult(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl">
      <h2 className="text-sm font-semibold text-gray-300 mb-6 tracking-wider uppercase">
        Strategy Backtest
      </h2>

      <div className="grid grid-cols-2 gap-4 mb-6">
        {[
          { key: "start_date", label: "Start date", type: "date" },
          { key: "end_date", label: "End date", type: "date" },
          { key: "dip_score_threshold", label: "Min dip score", type: "number" },
          { key: "hold_days", label: "Hold days", type: "number" },
        ].map(({ key, label, type }) => (
          <label key={key} className="flex flex-col gap-1">
            <span className="text-xs text-gray-500 uppercase tracking-widest">{label}</span>
            <input
              type={type}
              value={(params as Record<string, unknown>)[key] as string}
              onChange={(e) =>
                setParams((p) => ({
                  ...p,
                  [key]: type === "number" ? Number(e.target.value) : e.target.value,
                }))
              }
              className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm
                         text-gray-200 focus:border-emerald-500 focus:outline-none"
            />
          </label>
        ))}
      </div>

      <button
        onClick={run}
        disabled={loading}
        className="px-6 py-2 bg-emerald-600 hover:bg-emerald-500 text-gray-950 text-sm
                   font-semibold rounded transition-colors disabled:opacity-50"
      >
        {loading ? "Running…" : "Run Backtest"}
      </button>

      {result && <BacktestResults result={result} />}
    </div>
  );
}

function BacktestResults({ result }: { result: import("../api").BacktestResult }) {
  const metrics = [
    { label: "Total trades", value: result.total_trades, fmt: (v: number) => v.toString() },
    { label: "Win rate", value: result.win_rate, fmt: (v: number) => `${v.toFixed(1)}%` },
    { label: "Avg return", value: result.avg_return, fmt: (v: number) => `${v.toFixed(2)}%` },
    { label: "Max drawdown", value: result.max_drawdown, fmt: (v: number) => `${v.toFixed(2)}%` },
    { label: "Profit factor", value: result.profit_factor, fmt: (v: number) => v.toFixed(2) },
    { label: "Sharpe ratio", value: result.sharpe_ratio, fmt: (v: number) => v.toFixed(2) },
  ];

  return (
    <div className="mt-8">
      <div className="grid grid-cols-3 gap-3 mb-6">
        {metrics.map(({ label, value, fmt }) => (
          <div key={label} className="bg-gray-900 border border-gray-800 rounded p-3">
            <div className="text-xs text-gray-500 mb-1 uppercase tracking-widest">{label}</div>
            <div
              className={`text-xl font-semibold ${
                label === "Win rate" && value > 55
                  ? "text-emerald-400"
                  : label === "Max drawdown" && value < -15
                  ? "text-red-400"
                  : "text-gray-200"
              }`}
            >
              {fmt(value)}
            </div>
          </div>
        ))}
      </div>

      {/* Simple equity curve */}
      {result.equity_curve.length > 1 && (
        <div className="bg-gray-900 border border-gray-800 rounded p-4">
          <div className="text-xs text-gray-500 mb-3 uppercase tracking-widest">Equity curve</div>
          <EquityCurve data={result.equity_curve} />
        </div>
      )}
    </div>
  );
}

function EquityCurve({ data }: { data: { trade: number; equity: number }[] }) {
  const { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } =
    require("recharts");
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data}>
        <XAxis dataKey="trade" tick={{ fill: "#6b7280", fontSize: 10 }} />
        <YAxis tick={{ fill: "#6b7280", fontSize: 10 }} domain={["auto", "auto"]} />
        <Tooltip
          contentStyle={{ background: "#111827", border: "1px solid #374151", fontSize: 11 }}
          labelFormatter={(v) => `Trade #${v}`}
          formatter={(v: number) => [`$${v.toFixed(3)}`, "Equity"]}
        />
        <Line
          type="monotone"
          dataKey="equity"
          stroke="#10b981"
          strokeWidth={1.5}
          dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
