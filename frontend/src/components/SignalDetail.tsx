import type { Signal } from "../api";
import { useStore } from "../store";

interface Props {
  signal: Signal;
  onClose: () => void;
}

function Row({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="flex justify-between py-1.5 border-b border-gray-800 last:border-0">
      <span className="text-xs text-gray-500">{label}</span>
      <span className={`text-xs font-mono ${highlight ? "text-emerald-400" : "text-gray-300"}`}>
        {value}
      </span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <div className="text-[10px] text-gray-600 uppercase tracking-widest mb-2">{title}</div>
      {children}
    </div>
  );
}

function ScoreGauge({ label, value, colour }: { label: string; value: number | null; colour: string }) {
  const v = value ?? 0;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between">
        <span className="text-[10px] text-gray-500">{label}</span>
        <span className="text-[10px] text-gray-300">{v.toFixed(0)}</span>
      </div>
      <div className="h-1.5 bg-gray-800 rounded-full">
        <div className={`h-full rounded-full ${colour}`} style={{ width: `${Math.min(100, v)}%` }} />
      </div>
    </div>
  );
}

const BIAS_COLOURS: Record<string, string> = {
  BUY:   "text-emerald-400 border-emerald-600",
  WATCH: "text-amber-400 border-amber-600",
  AVOID: "text-red-400 border-red-600",
};

export function SignalDetail({ signal: s, onClose }: Props) {
  const { addToWatchlist } = useStore();
  const t = s.technicals;
  const l = s.levels;

  const priceFmt = (v: number | null | undefined) =>
    v != null ? `$${v.toFixed(2)}` : "—";
  const pctFmt = (v: number | null | undefined) =>
    v != null ? `${v.toFixed(1)}%` : "—";

  return (
    <div className="bg-gray-950 border border-gray-800 rounded-lg sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-gray-800">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-gray-100">{s.symbol}</span>
            <span
              className={`text-xs font-bold px-2 py-0.5 rounded border ${
                BIAS_COLOURS[s.bias ?? "AVOID"]
              }`}
            >
              {s.bias}
            </span>
          </div>
          <div className="text-xs text-gray-500 mt-0.5">{s.name ?? "—"}</div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => addToWatchlist(s.symbol)}
            className="text-xs px-2 py-1 border border-gray-700 rounded hover:border-emerald-600
                       hover:text-emerald-400 transition-colors"
            title="Add to watchlist"
          >
            + Watch
          </button>
          <button
            onClick={onClose}
            className="text-gray-600 hover:text-gray-300 text-lg leading-none"
          >
            ×
          </button>
        </div>
      </div>

      <div className="p-4">
        {/* Score overview */}
        <Section title="Signal scores">
          <div className="space-y-2">
            <ScoreGauge label="Composite"  value={s.composite_score}  colour="bg-emerald-500" />
            <ScoreGauge label="Dip score"  value={s.dip_score}        colour="bg-blue-500" />
            <ScoreGauge label="Sentiment"  value={s.sentiment_score}  colour="bg-purple-500" />
            <ScoreGauge label="Confidence" value={s.confidence}       colour="bg-amber-500" />
          </div>
        </Section>

        {/* Trade levels */}
        {l && (
          <Section title="Trade levels">
            <Row label="Entry zone" value={`${priceFmt(l.entry_low)} – ${priceFmt(l.entry_high)}`} highlight />
            <Row label="Stop loss"  value={priceFmt(l.stop_loss)} />
            <Row label="Target 1"   value={priceFmt(l.target_1)} />
            <Row label="Target 2"   value={priceFmt(l.target_2)} />
            <Row label="Risk / Reward" value={l.risk_reward != null ? `${l.risk_reward.toFixed(1)}x` : "—"} highlight />
          </Section>
        )}

        {/* Technicals */}
        {t && (
          <Section title="Technical indicators">
            <Row label="Price"       value={priceFmt(t.price)} />
            <Row label="RSI (14)"    value={t.rsi_14?.toFixed(1) ?? "—"} highlight={(t.rsi_14 ?? 50) < 30} />
            <Row label="Bollinger %B" value={t.bb_pct?.toFixed(3) ?? "—"} />
            <Row label="MA 20"       value={priceFmt(t.ma20)} />
            <Row label="MA 50"       value={priceFmt(t.ma50)} />
            <Row label="MA 200"      value={priceFmt(t.ma200)} />
            <Row label="ATR"         value={priceFmt(t.atr)} />
            <Row label="Z-score"     value={t.zscore?.toFixed(2) ?? "—"} highlight={(t.zscore ?? 0) < -1.5} />
          </Section>
        )}

        {/* Sub-scores breakdown */}
        <Section title="Score breakdown">
          {[
            { label: "RSI oversold",     value: s.rsi_score },
            { label: "Bollinger Band",   value: s.bb_score },
            { label: "MA deviation",     value: s.ma_deviation_score },
            { label: "Volume spike",     value: s.volume_score },
            { label: "Z-score",          value: s.zscore_score },
            { label: "Mean reversion",   value: s.mean_rev_score },
            { label: "Support zone",     value: s.support_score },
          ].map(({ label, value }) => (
            <div key={label} className="flex items-center gap-2 py-1">
              <span className="text-xs text-gray-500 w-32 shrink-0">{label}</span>
              <div className="flex-1 h-1 bg-gray-800 rounded-full">
                <div
                  className="h-full bg-blue-600 rounded-full"
                  style={{ width: `${Math.min(100, value ?? 0)}%` }}
                />
              </div>
              <span className="text-xs text-gray-400 w-8 text-right">
                {(value ?? 0).toFixed(0)}
              </span>
            </div>
          ))}
        </Section>

        {/* Market context */}
        <Section title="Market context">
          <Row label="SPY trend"   value={s.spy_trend ?? "—"} />
          <Row label="VIX"         value={s.vix_level?.toFixed(1) ?? "—"} />
          <Row label="Sector RS"   value={s.sector_rs ? `${s.sector_rs.toFixed(2)}x` : "—"} />
          <Row label="Sector"      value={s.sector ?? "—"} />
        </Section>

        {/* Sentiment */}
        <Section title="News & sentiment">
          <Row label="Label"     value={s.sentiment_label ?? "neutral"} />
          <Row label="News type" value={s.news_type?.replace("_", " ") ?? "—"} />
        </Section>

        {/* News headlines */}
        {s.news && s.news.length > 0 && (
          <Section title="Recent headlines">
            <div className="space-y-2">
              {s.news.slice(0, 5).map((n, i) => (
                <div key={i} className="text-xs">
                  <a
                    href={n.url ?? "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-gray-400 hover:text-emerald-400 transition-colors leading-relaxed"
                  >
                    {n.title}
                  </a>
                  <div className="text-gray-600 mt-0.5">
                    {n.source}
                    {n.published_at && ` · ${new Date(n.published_at).toLocaleDateString()}`}
                  </div>
                </div>
              ))}
            </div>
          </Section>
        )}

        {/* Reasoning */}
        {s.reasoning && (
          <Section title="AI reasoning">
            <p className="text-xs text-gray-400 leading-relaxed">{s.reasoning}</p>
          </Section>
        )}
      </div>
    </div>
  );
}
