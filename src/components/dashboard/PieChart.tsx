"use client";

import { useState } from "react";

interface PieSlice {
  label: string;
  value: number;
  color: string;
}

interface PieChartProps {
  data: PieSlice[];
}

function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return {
    x: cx + r * Math.cos(rad),
    y: cy + r * Math.sin(rad),
  };
}

function arcPath(cx: number, cy: number, r: number, startAngle: number, endAngle: number): string {
  const start = polarToCartesian(cx, cy, r, endAngle);
  const end = polarToCartesian(cx, cy, r, startAngle);
  const largeArc = endAngle - startAngle > 180 ? 1 : 0;
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 0 ${end.x} ${end.y}`;
}

export function PieChart({ data }: PieChartProps) {
  const [hovered, setHovered] = useState<number | null>(null);

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-sm text-[hsl(var(--muted-foreground))]">
        No data
      </div>
    );
  }

  const total = data.reduce((s, d) => s + d.value, 0);
  const size = 200;
  const cx = size / 2;
  const cy = size / 2;
  const outerR = 75;
  const innerR = 42; // donut hole

  let cursor = 0;
  const slices = data.map((d, i) => {
    const pct = d.value / total;
    const startAngle = cursor * 360;
    cursor += pct;
    const endAngle = cursor * 360;
    return { ...d, startAngle, endAngle, pct, i };
  });

  return (
    <div className="flex items-center gap-6 flex-wrap">
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size}>
        {slices.map((s) => {
          const isHovered = hovered === s.i;
          const r = isHovered ? outerR + 5 : outerR;
          const path = arcPath(cx, cy, r, s.startAngle, s.endAngle);
          const holePath = arcPath(cx, cy, innerR, s.startAngle, s.endAngle);

          return (
            <path
              key={s.i}
              d={`${path} L ${cx} ${cy} Z`}
              fill={s.color}
              fillOpacity={isHovered ? 1 : 0.85}
              stroke="hsl(var(--background))"
              strokeWidth="2"
              style={{ cursor: "pointer", transition: "all 0.15s" }}
              onMouseEnter={() => setHovered(s.i)}
              onMouseLeave={() => setHovered(null)}
            >
              <title>
                {s.label}: {s.value} ({(s.pct * 100).toFixed(1)}%)
              </title>
            </path>
          );
        })}
        {/* Donut hole */}
        <circle cx={cx} cy={cy} r={innerR} fill="hsl(var(--card))" />
        {/* Center text */}
        <text x={cx} y={cy - 6} textAnchor="middle" fontSize="13" fontWeight="600" fill="hsl(var(--foreground))">
          {total}
        </text>
        <text x={cx} y={cy + 10} textAnchor="middle" fontSize="10" fill="hsl(var(--muted-foreground))">
          total
        </text>
      </svg>

      {/* Legend */}
      <div className="flex flex-col gap-1.5">
        {slices.map((s) => (
          <div
            key={s.i}
            className="flex items-center gap-2 text-xs cursor-pointer"
            onMouseEnter={() => setHovered(s.i)}
            onMouseLeave={() => setHovered(null)}
          >
            <span
              className="inline-block w-3 h-3 rounded-sm shrink-0"
              style={{ backgroundColor: s.color }}
            />
            <span className="text-[hsl(var(--foreground))]">{s.label}</span>
            <span className="text-[hsl(var(--muted-foreground))] ml-1">
              {(s.pct * 100).toFixed(0)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
