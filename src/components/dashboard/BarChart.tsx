"use client";

interface BarData {
  label: string;
  value: number;
}

interface BarChartProps {
  data: BarData[];
  horizontal?: boolean;
  color?: string;
}

const COLORS = ["#10b981", "#6366f1", "#f59e0b", "#ef4444", "#3b82f6", "#8b5cf6"];

export function BarChart({ data, horizontal = false, color }: BarChartProps) {
  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-sm text-[hsl(var(--muted-foreground))]">
        No data
      </div>
    );
  }

  const maxVal = Math.max(...data.map((d) => d.value));

  if (horizontal) {
    return (
      <div className="flex flex-col gap-2 w-full">
        {data.map((d, i) => {
          const pct = maxVal > 0 ? (d.value / maxVal) * 100 : 0;
          const barColor = color ?? COLORS[i % COLORS.length];
          return (
            <div key={i} className="flex items-center gap-2">
              <span
                className="text-xs text-[hsl(var(--muted-foreground))] text-right shrink-0"
                style={{ width: 72 }}
              >
                {d.label}
              </span>
              <div className="flex-1 h-5 bg-[hsl(var(--muted))] rounded overflow-hidden">
                <div
                  className="h-full rounded transition-all duration-500"
                  style={{ width: `${pct}%`, backgroundColor: barColor }}
                />
              </div>
              <span className="text-xs text-[hsl(var(--foreground))] shrink-0 w-8 text-right">
                {d.value}
              </span>
            </div>
          );
        })}
      </div>
    );
  }

  // Vertical SVG bar chart
  const width = 360;
  const height = 180;
  const padLeft = 32;
  const padRight = 12;
  const padTop = 12;
  const padBottom = 32;
  const chartW = width - padLeft - padRight;
  const chartH = height - padTop - padBottom;
  const barW = (chartW / data.length) * 0.6;
  const gap = chartW / data.length;

  const yTicks = [0, Math.round(maxVal * 0.5), maxVal];

  return (
    <div className="w-full">
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height}>
        {/* Grid lines */}
        {yTicks.map((v) => {
          const y = padTop + chartH - (maxVal > 0 ? (v / maxVal) * chartH : 0);
          return (
            <g key={v}>
              <line
                x1={padLeft}
                y1={y}
                x2={padLeft + chartW}
                y2={y}
                stroke="hsl(var(--border))"
                strokeWidth="1"
              />
              <text x={padLeft - 4} y={y + 4} textAnchor="end" fontSize="10" fill="hsl(var(--muted-foreground))">
                {v}
              </text>
            </g>
          );
        })}

        {/* Bars */}
        {data.map((d, i) => {
          const barH = maxVal > 0 ? (d.value / maxVal) * chartH : 0;
          const x = padLeft + i * gap + (gap - barW) / 2;
          const y = padTop + chartH - barH;
          const barColor = color ?? COLORS[i % COLORS.length];
          return (
            <g key={i}>
              <rect
                x={x}
                y={y}
                width={barW}
                height={barH}
                rx="3"
                fill={barColor}
                fillOpacity="0.85"
              />
              <text
                x={x + barW / 2}
                y={padTop + chartH + 16}
                textAnchor="middle"
                fontSize="10"
                fill="hsl(var(--muted-foreground))"
              >
                {d.label.length > 7 ? d.label.slice(0, 6) + "…" : d.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
