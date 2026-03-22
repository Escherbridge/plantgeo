"use client";

interface StatCardProps {
  label: string;
  value: number;
  trend: "up" | "down" | "neutral";
  trendValue?: number;
  sparkline?: number[];
  color?: string;
}

export function StatCard({ label, value, trend, trendValue, sparkline = [], color = "#10b981" }: StatCardProps) {
  const trendIcon = trend === "up" ? "▲" : trend === "down" ? "▼" : "—";
  const trendColor =
    trend === "up"
      ? "text-red-400"
      : trend === "down"
      ? "text-emerald-400"
      : "text-[hsl(var(--muted-foreground))]";

  // Mini sparkline SVG
  const sparkW = 64;
  const sparkH = 28;
  const padX = 2;
  const padY = 3;

  let sparkPath = "";
  if (sparkline.length > 1) {
    const min = Math.min(...sparkline);
    const max = Math.max(...sparkline);
    const range = max - min || 1;
    const innerW = sparkW - padX * 2;
    const innerH = sparkH - padY * 2;
    const pts = sparkline.map((v, i) => {
      const x = padX + (i / (sparkline.length - 1)) * innerW;
      const y = padY + innerH - ((v - min) / range) * innerH;
      return `${x},${y}`;
    });
    sparkPath = pts.join(" ");
  }

  return (
    <div className="rounded-xl border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-4 py-3 flex items-center justify-between gap-3">
      <div className="flex flex-col gap-1">
        <span className="text-xs text-[hsl(var(--muted-foreground))] font-medium uppercase tracking-wide">
          {label}
        </span>
        <span className="text-3xl font-bold text-[hsl(var(--foreground))]">{value.toLocaleString()}</span>
        {trendValue !== undefined && (
          <span className={`text-xs font-medium flex items-center gap-0.5 ${trendColor}`}>
            {trendIcon} {trendValue} from yesterday
          </span>
        )}
      </div>

      {sparkline.length > 1 && (
        <svg width={sparkW} height={sparkH} viewBox={`0 0 ${sparkW} ${sparkH}`} className="shrink-0">
          <polyline
            points={sparkPath}
            fill="none"
            stroke={color}
            strokeWidth="1.5"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        </svg>
      )}
    </div>
  );
}
