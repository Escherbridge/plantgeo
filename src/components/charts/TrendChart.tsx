// npm install recharts @react-pdf/renderer
"use client";

import { useState } from "react";

export interface TrendPoint {
  date: string;
  value: number;
}

interface TrendChartProps {
  data: TrendPoint[];
  title: string;
  color: string;
  unit: string;
}

const WIDTH = 480;
const HEIGHT = 160;
const PADDING = { top: 16, right: 16, bottom: 36, left: 44 };

function formatDateLabel(date: string): string {
  const d = new Date(date);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function TrendChart({ data, title, color, unit }: TrendChartProps) {
  const [tooltip, setTooltip] = useState<{
    x: number;
    y: number;
    point: TrendPoint;
  } | null>(null);

  if (!data || data.length === 0) {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-xs font-medium text-[hsl(var(--muted-foreground))]">{title}</p>
        <div className="flex items-center justify-center h-40 text-xs text-[hsl(var(--muted-foreground))] bg-[hsl(var(--muted))] rounded-lg">
          No data available
        </div>
      </div>
    );
  }

  const values = data.map((d) => d.value);
  const minVal = Math.min(...values);
  const maxVal = Math.max(...values);
  const valRange = maxVal - minVal || 1;

  const innerW = WIDTH - PADDING.left - PADDING.right;
  const innerH = HEIGHT - PADDING.top - PADDING.bottom;

  function xPos(i: number): number {
    return PADDING.left + (i / Math.max(data.length - 1, 1)) * innerW;
  }

  function yPos(v: number): number {
    return PADDING.top + (1 - (v - minVal) / valRange) * innerH;
  }

  const pathD = data
    .map((d, i) => `${i === 0 ? "M" : "L"} ${xPos(i).toFixed(1)} ${yPos(d.value).toFixed(1)}`)
    .join(" ");

  const areaD =
    pathD +
    ` L ${xPos(data.length - 1).toFixed(1)} ${(PADDING.top + innerH).toFixed(1)}` +
    ` L ${PADDING.left.toFixed(1)} ${(PADDING.top + innerH).toFixed(1)} Z`;

  // Y-axis tick values
  const yTicks = [minVal, minVal + valRange / 2, maxVal].map((v) => Math.round(v * 10) / 10);

  // X-axis: show up to 5 labels
  const xTickIndices = data.length <= 5
    ? data.map((_, i) => i)
    : [0, Math.floor(data.length / 4), Math.floor(data.length / 2), Math.floor((3 * data.length) / 4), data.length - 1];

  return (
    <div className="flex flex-col gap-1 w-full">
      <p className="text-xs font-medium text-[hsl(var(--muted-foreground))]">{title}</p>
      <div className="w-full overflow-x-auto">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="w-full"
          style={{ minWidth: 240, maxHeight: 160 }}
          onMouseLeave={() => setTooltip(null)}
        >
          {/* Grid lines */}
          {yTicks.map((tick, i) => (
            <line
              key={i}
              x1={PADDING.left}
              x2={WIDTH - PADDING.right}
              y1={yPos(tick)}
              y2={yPos(tick)}
              stroke="hsl(var(--border))"
              strokeWidth={0.5}
              strokeDasharray="4 4"
            />
          ))}

          {/* Area fill */}
          <path d={areaD} fill={color} fillOpacity={0.12} />

          {/* Line */}
          <path d={pathD} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />

          {/* Data points + hover targets */}
          {data.map((point, i) => (
            <circle
              key={i}
              cx={xPos(i)}
              cy={yPos(point.value)}
              r={4}
              fill={color}
              stroke="white"
              strokeWidth={1.5}
              className="cursor-pointer"
              onMouseEnter={() =>
                setTooltip({ x: xPos(i), y: yPos(point.value), point })
              }
            />
          ))}

          {/* Y-axis labels */}
          {yTicks.map((tick, i) => (
            <text
              key={i}
              x={PADDING.left - 6}
              y={yPos(tick) + 4}
              textAnchor="end"
              fontSize={9}
              fill="currentColor"
              className="text-[hsl(var(--muted-foreground))]"
            >
              {tick}
            </text>
          ))}

          {/* X-axis labels */}
          {xTickIndices.map((idx) => (
            <text
              key={idx}
              x={xPos(idx)}
              y={HEIGHT - 4}
              textAnchor="middle"
              fontSize={8}
              fill="currentColor"
              className="text-[hsl(var(--muted-foreground))]"
            >
              {formatDateLabel(data[idx].date)}
            </text>
          ))}

          {/* Tooltip */}
          {tooltip && (
            <g>
              <line
                x1={tooltip.x}
                x2={tooltip.x}
                y1={PADDING.top}
                y2={PADDING.top + innerH}
                stroke={color}
                strokeWidth={1}
                strokeDasharray="3 3"
              />
              <rect
                x={Math.min(tooltip.x + 6, WIDTH - 90)}
                y={tooltip.y - 20}
                width={84}
                height={32}
                rx={4}
                fill="hsl(var(--popover))"
                stroke="hsl(var(--border))"
                strokeWidth={1}
              />
              <text
                x={Math.min(tooltip.x + 48, WIDTH - 48)}
                y={tooltip.y - 7}
                textAnchor="middle"
                fontSize={9}
                fill="currentColor"
              >
                {formatDateLabel(tooltip.point.date)}
              </text>
              <text
                x={Math.min(tooltip.x + 48, WIDTH - 48)}
                y={tooltip.y + 7}
                textAnchor="middle"
                fontSize={10}
                fontWeight="bold"
                fill={color}
              >
                {tooltip.point.value} {unit}
              </text>
            </g>
          )}
        </svg>
      </div>
    </div>
  );
}
