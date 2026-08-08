"use client";

import { useState } from "react";
import { buildLinearScales, selectXLabelIndices } from "@/components/dashboard/chart-scales";

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
  const padTop = 12;
  const { scaleX, scaleY, chartWidth, chartHeight, ticks, decimals } = buildLinearScales(
    data.map((d) => d.value),
    data.length,
    { width, height, padLeft, padRight: 12, padTop, padBottom: 28 }
  );
  const formatValue = (v: number) => v.toFixed(decimals);

  const points = data.map((d, i) => `${scaleX(i)},${scaleY(d.value)}`).join(" ");

  // Area polygon: line points + bottom-right + bottom-left
  const areaPoints = [
    ...data.map((d, i) => `${scaleX(i)},${scaleY(d.value)}`),
    `${scaleX(data.length - 1)},${padTop + chartHeight}`,
    `${scaleX(0)},${padTop + chartHeight}`,
  ].join(" ");

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
        {ticks.map(({ y }, index) => (
          <line
            key={index}
            x1={padLeft}
            y1={y}
            x2={padLeft + chartWidth}
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
            onMouseEnter={() => {
              setTooltip({
                x: scaleX(i),
                y: scaleY(d.value),
                point: d,
              });
            }}
          />
        ))}

        {/* Y-axis labels */}
        {ticks.map(({ value, y }, index) => (
          <text
            key={index}
            x={padLeft - 4}
            y={y + 4}
            textAnchor="end"
            fontSize="10"
            fill="hsl(var(--muted-foreground))"
          >
            {formatValue(value)}
          </text>
        ))}

        {/* X-axis labels */}
        {selectXLabelIndices(data.length).map((idx) => (
          <text
            key={idx}
            x={scaleX(idx)}
            y={padTop + chartHeight + 18}
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
              y2={padTop + chartHeight}
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
