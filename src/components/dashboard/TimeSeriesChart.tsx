"use client";

import { useState } from "react";

interface DataPoint {
  time: string;
  value: number;
}

interface TimeSeriesChartProps {
  data: DataPoint[];
  width: number;
  height: number;
  color: string;
}

export function TimeSeriesChart({ data, width, height, color }: TimeSeriesChartProps) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; point: DataPoint } | null>(null);

  if (data.length === 0) {
    return (
      <div
        style={{ width, height }}
        className="flex items-center justify-center text-sm text-[hsl(var(--muted-foreground))]"
      >
        No data
      </div>
    );
  }

  const padLeft = 36;
  const padRight = 12;
  const padTop = 12;
  const padBottom = 28;
  const chartW = width - padLeft - padRight;
  const chartH = height - padTop - padBottom;

  const values = data.map((d) => d.value);
  const minVal = Math.min(...values);
  const maxVal = Math.max(...values);
  const range = maxVal - minVal || 1;

  const scaleX = (i: number) => padLeft + (i / (data.length - 1)) * chartW;
  const scaleY = (v: number) => padTop + chartH - ((v - minVal) / range) * chartH;

  const points = data.map((d, i) => `${scaleX(i)},${scaleY(d.value)}`).join(" ");

  // Area polygon: line points + bottom-right + bottom-left
  const areaPoints = [
    ...data.map((d, i) => `${scaleX(i)},${scaleY(d.value)}`),
    `${scaleX(data.length - 1)},${padTop + chartH}`,
    `${scaleX(0)},${padTop + chartH}`,
  ].join(" ");

  // Y-axis ticks
  const yTicks = [minVal, minVal + range * 0.5, maxVal].map((v) => ({
    v,
    y: scaleY(v),
  }));

  // X-axis labels — show up to 6 evenly spaced
  const xLabelCount = Math.min(data.length, 6);
  const xLabelIndices = Array.from({ length: xLabelCount }, (_, i) =>
    Math.round((i / (xLabelCount - 1)) * (data.length - 1))
  );

  return (
    <div className="relative w-full" style={{ maxWidth: width }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        style={{ overflow: "visible" }}
        onMouseLeave={() => setTooltip(null)}
      >
        {/* Area fill */}
        <defs>
          <linearGradient id={`area-grad-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.25" />
            <stop offset="100%" stopColor={color} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <polygon
          points={areaPoints}
          fill={`url(#area-grad-${color.replace("#", "")})`}
        />

        {/* Grid lines */}
        {yTicks.map(({ v, y }) => (
          <line
            key={v}
            x1={padLeft}
            y1={y}
            x2={padLeft + chartW}
            y2={y}
            stroke="hsl(var(--border))"
            strokeWidth="1"
          />
        ))}

        {/* Line */}
        <polyline
          points={points}
          fill="none"
          stroke={color}
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* Data points */}
        {data.map((d, i) => (
          <circle
            key={i}
            cx={scaleX(i)}
            cy={scaleY(d.value)}
            r={tooltip?.point === d ? 5 : 3}
            fill={color}
            stroke="hsl(var(--background))"
            strokeWidth="1.5"
            style={{ cursor: "pointer" }}
            onMouseEnter={(e) => {
              const rect = e.currentTarget.closest("svg")!.getBoundingClientRect();
              setTooltip({
                x: scaleX(i),
                y: scaleY(d.value),
                point: d,
              });
            }}
          />
        ))}

        {/* Y-axis labels */}
        {yTicks.map(({ v, y }) => (
          <text
            key={v}
            x={padLeft - 4}
            y={y + 4}
            textAnchor="end"
            fontSize="10"
            fill="hsl(var(--muted-foreground))"
          >
            {Math.round(v)}
          </text>
        ))}

        {/* X-axis labels */}
        {xLabelIndices.map((idx) => (
          <text
            key={idx}
            x={scaleX(idx)}
            y={padTop + chartH + 18}
            textAnchor="middle"
            fontSize="10"
            fill="hsl(var(--muted-foreground))"
          >
            {data[idx].time}
          </text>
        ))}

        {/* Tooltip */}
        {tooltip && (
          <g>
            <line
              x1={tooltip.x}
              y1={padTop}
              x2={tooltip.x}
              y2={padTop + chartH}
              stroke={color}
              strokeWidth="1"
              strokeDasharray="3,3"
            />
            <rect
              x={tooltip.x + 6}
              y={tooltip.y - 22}
              width={72}
              height={22}
              rx="4"
              fill="hsl(var(--card))"
              stroke="hsl(var(--border))"
            />
            <text
              x={tooltip.x + 10}
              y={tooltip.y - 6}
              fontSize="10"
              fill="hsl(var(--foreground))"
            >
              {tooltip.point.time}: {tooltip.point.value}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}
