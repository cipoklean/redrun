import { cn } from "@/lib/utils";

interface Props {
  confidence: number;
  cause: string;
  size?: number;
}

/** A 270 degree arc. The number is computed by the engine, never by the model. */
export function ConfidenceGauge({ confidence, cause, size = 128 }: Props) {
  const pct = Math.max(0, Math.min(1, confidence));
  const radius = (size - 14) / 2;
  const circumference = 2 * Math.PI * radius;
  const arc = circumference * 0.75;
  const filled = arc * pct;
  const tone = pct >= 0.7 ? "hsl(var(--pass))" : pct >= 0.45 ? "hsl(var(--warn))" : "hsl(var(--fail))";

  return (
    <div className="flex items-center gap-4">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`Computed confidence ${(pct * 100).toFixed(1)} percent`}
      >
        <g transform={`rotate(135 ${size / 2} ${size / 2})`}>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="hsl(var(--line))"
            strokeWidth={8}
            strokeDasharray={`${arc} ${circumference}`}
            strokeLinecap="round"
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={tone}
            strokeWidth={8}
            strokeDasharray={`${filled} ${circumference}`}
            strokeLinecap="round"
          />
        </g>
        <text
          x="50%"
          y="48%"
          textAnchor="middle"
          dominantBaseline="middle"
          className="fill-ink font-mono"
          style={{ fontSize: size * 0.22, fontWeight: 600 }}
        >
          {(pct * 100).toFixed(0)}%
        </text>
        <text
          x="50%"
          y="66%"
          textAnchor="middle"
          className={cn("fill-[hsl(var(--muted))]")}
          style={{ fontSize: size * 0.085, letterSpacing: "0.08em" }}
        >
          {cause.toUpperCase()}
        </text>
      </svg>
      <div className="text-[12px] leading-relaxed text-muted">
        <p className="font-medium text-ink">computed from signals</p>
        <p>not model-reported</p>
      </div>
    </div>
  );
}
