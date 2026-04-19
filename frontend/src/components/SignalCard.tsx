import type { Signal } from "../api";

const BIAS_STYLES: Record<string, string> = {
  BUY:   "bg-emerald-900 text-emerald-300 border-emerald-700",
  WATCH: "bg-amber-900 text-amber-300 border-amber-700",
  AVOID: "bg-red-900 text-red-300 border-red-700",
};

const SENTIMENT_COLOURS: Record<string, string> = {
  positive: "text-emerald-400",
  neutral:  "text-gray-400",
  negative: "text-red-400",
};

function ScoreBar({ value, colour }: { value: number | null; colour: string }) {
  const v = value ?? 0;
  return (
    <div className="w-16 h-1 bg-gray-800 rounded-full overflow-hidden">
      <div
        className={`h-full rounded-full transition-all ${colour}`}
        style={{ width: `${Math.min(100, v)}%` }}
      />
    </div>
  );
}

interface Props {
  signal: Signal;
  isSelected: boolean;
  onClick: () => void;
}

export function SignalCard({ signal: s, isSelected, onClick }: Props) {
  const price = s.technicals?.price ?? s.levels?.entry_low;
  const rr = s.levels?.risk_reward;

  return (
    <div
      onClick={onClick}
      className={`
        rounded border cursor-pointer transition-all
        ${isSelected
          ? "border-emerald-600 bg-gray-900"
          : "border-gray-800 bg-gray-950 hover:border-gray-700 hover:bg-gray-900"
        }
      `}
    >
      <div className="px-4 py-3 flex items-center gap-4">
        {/* Bias badge */}
        <span
          className={`text-[10px] font-bold px-2 py-0.5 rounded border shrink-0 ${
            BIAS_STYLES[s.bias ?? "AVOID"]
          }`}
        >
          {s.bias ?? "—"}
        </span>

        {/* Symbol + name */}
        <div className="w-36 shrink-0">
          <div className="text-sm font-semibold text-gray-100">{s.symbol}</div>
          <div className="text-xs text-gray-500 truncate">{s.name ?? s.sector ?? "—"}</div>
        </div>

        {/* Price */}
        <div className="w-20 shrink-0 text-right">
          <div className="text-sm text-gray-200">
            {price != null ? `$${price.toFixed(2)}` : "—"}
          </div>
          <div className="text-xs text-gray-600">price</div>
        </div>

        {/* Composite score */}
        <div className="w-20 shrink-0">
          <div className="text-xs text-gray-500 mb-1">composite</div>
          <div className="flex items-center gap-2">
            <ScoreBar value={s.composite_score} colour="bg-emerald-500" />
            <span className="text-xs text-gray-300">{s.composite_score?.toFixed(0) ?? "—"}</span>
          </div>
        </div>

        {/* Dip score */}
        <div className="w-20 shrink-0 hidden sm:block">
          <div className="text-xs text-gray-500 mb-1">dip</div>
          <div className="flex items-center gap-2">
            <ScoreBar value={s.dip_score} colour="bg-blue-500" />
            <span className="text-xs text-gray-300">{s.dip_score?.toFixed(0) ?? "—"}</span>
          </div>
        </div>

        {/* Sentiment */}
        <div className="w-20 shrink-0 hidden md:block">
          <div className="text-xs text-gray-500 mb-1">sentiment</div>
          <div className="flex items-center gap-2">
            <ScoreBar value={s.sentiment_score} colour="bg-purple-500" />
            <span className="text-xs text-gray-300">{s.sentiment_score?.toFixed(0) ?? "—"}</span>
          </div>
        </div>

        {/* RSI */}
        <div className="w-16 shrink-0 text-center hidden lg:block">
          <div className="text-xs text-gray-500">RSI</div>
          <div
            className={`text-sm font-mono ${
              (s.technicals?.rsi_14 ?? 50) < 30 ? "text-red-400" : "text-gray-300"
            }`}
          >
            {s.technicals?.rsi_14?.toFixed(1) ?? "—"}
          </div>
        </div>

        {/* R:R */}
        <div className="w-16 shrink-0 text-center hidden lg:block">
          <div className="text-xs text-gray-500">R:R</div>
          <div className="text-sm font-mono text-gray-300">
            {rr != null ? `${rr.toFixed(1)}x` : "—"}
          </div>
        </div>

        {/* Confidence */}
        <div className="ml-auto text-right shrink-0">
          <div className="text-xs text-gray-500">confidence</div>
          <div className="text-sm font-semibold text-gray-200">
            {s.confidence?.toFixed(0) ?? "—"}%
          </div>
        </div>

        {/* Sentiment label */}
        <div
          className={`text-xs shrink-0 w-16 text-right hidden xl:block ${
            SENTIMENT_COLOURS[s.sentiment_label ?? "neutral"]
          }`}
        >
          {s.sentiment_label ?? "neutral"}
        </div>
      </div>
    </div>
  );
}
