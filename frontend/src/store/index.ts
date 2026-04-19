import { create } from "zustand";
import type { Signal, WatchlistEntry, MarketContext } from "./api";
import { signalsApi, watchlistApi, marketApi } from "./api";

interface AppState {
  // Signals
  signals: Signal[];
  totalSignals: number;
  signalsLoading: boolean;
  signalFilters: {
    bias?: string;
    min_dip_score: number;
    min_confidence: number;
    sort_by: string;
    page: number;
    page_size: number;
  };

  // Selected signal (detail panel)
  selectedSignal: Signal | null;

  // Watchlist
  watchlist: WatchlistEntry[];
  watchlistLoading: boolean;

  // Market context
  marketContext: MarketContext | null;

  // Scan status
  lastScanAt: string | null;
  scanRunning: boolean;

  // Actions
  fetchSignals: () => Promise<void>;
  setFilter: (key: string, value: unknown) => void;
  selectSignal: (sig: Signal | null) => void;
  fetchWatchlist: () => Promise<void>;
  addToWatchlist: (symbol: string) => Promise<void>;
  removeFromWatchlist: (id: string) => Promise<void>;
  fetchMarketContext: () => Promise<void>;
  triggerScan: () => Promise<void>;
  handleWsMessage: (msg: { type: string; payload: unknown }) => void;
}

export const useStore = create<AppState>((set, get) => ({
  signals: [],
  totalSignals: 0,
  signalsLoading: false,
  signalFilters: {
    min_dip_score: 0,
    min_confidence: 0,
    sort_by: "composite_score",
    page: 1,
    page_size: 25,
  },

  selectedSignal: null,
  watchlist: [],
  watchlistLoading: false,
  marketContext: null,
  lastScanAt: null,
  scanRunning: false,

  fetchSignals: async () => {
    set({ signalsLoading: true });
    try {
      const { data } = await signalsApi.list(get().signalFilters);
      set({
        signals: data.signals,
        totalSignals: data.total,
        signalsLoading: false,
      });
    } catch {
      set({ signalsLoading: false });
    }
  },

  setFilter: (key, value) => {
    set((s) => ({
      signalFilters: {
        ...s.signalFilters,
        [key]: value,
        page: key !== "page" ? 1 : (value as number),
      },
    }));
    get().fetchSignals();
  },

  selectSignal: (sig) => set({ selectedSignal: sig }),

  fetchWatchlist: async () => {
    set({ watchlistLoading: true });
    try {
      const { data } = await watchlistApi.list();
      set({ watchlist: data, watchlistLoading: false });
    } catch {
      set({ watchlistLoading: false });
    }
  },

  addToWatchlist: async (symbol) => {
    await watchlistApi.add(symbol);
    get().fetchWatchlist();
  },

  removeFromWatchlist: async (id) => {
    await watchlistApi.remove(id);
    set((s) => ({ watchlist: s.watchlist.filter((w) => w.id !== id) }));
  },

  fetchMarketContext: async () => {
    try {
      const { data } = await marketApi.context();
      set({ marketContext: data });
    } catch {
      // silently fail — non-critical
    }
  },

  triggerScan: async () => {
    set({ scanRunning: true });
    try {
      await marketApi.triggerScan();
    } finally {
      set({ scanRunning: false });
    }
  },

  handleWsMessage: (msg) => {
    if (msg.type === "scan_complete") {
      set({ lastScanAt: new Date().toISOString() });
      get().fetchSignals();
    }
    if (msg.type === "signal_update") {
      const updated = msg.payload as Signal;
      set((s) => ({
        signals: s.signals.map((sig) =>
          sig.id === updated.id ? updated : sig
        ),
      }));
    }
  },
}));
