"use client";

import { useState } from "react";
import {
  buildLinearScales,
  hasFullBand,
  selectXLabelIndices,
} from "@/components/dashboard/chart-scales";

interface BandPoint {
  time: string;
  median: number;
  low: number | null;
  high: number | null;
}

interface ForecastBandChartProps {
  data: BandPoint[];
  width: number;
  height: number;
  color: string;
  unit?: string;
}

/** Sibling of TimeSeriesChart over the shared chart-scales, plus a p10–p90 band under the median line. */
export function ForecastBandChart({ data, width, height, color, unit }: ForecastBandChartProps) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; point: BandPoint } | null>(null);

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

  const padLeft = 44;
  const padTop = 12;
  // The scale must hold the band, not just the median, or the band clips at the frame.
  const extremes = data.flatMap((d) => [d.median, d.low ?? d.median, d.high ?? d.median]);
  const { scaleX, scaleY, chartWidth, chartHeight, ticks, decimals } = buildLinearScales(
    extremes,
    data.length,
    { width, height, padLeft, padRight: 12, padTop, padBottom: 28 }
  );
  const formatValue = (v: number) => v.toFixed(decimals);

  // A polygon needs a gap-free band; a partial quantile set draws the median alone.
  const fullBand = hasFullBand(data);
  // With one point the band polygon has zero area, so it draws as a whisker instead.
  const singlePointBand = fullBand && data.length === 1 ? data[0] : null;
  const bandPoints =
    fullBand && data.length > 1
      ? [
          ...data.map((d, i) => `${scaleX(i)},${scaleY(d.high as number)}`),
          ...data.map((d, i) => `${scaleX(i)},${scaleY(d.low as number)}`).reverse(),
        ].join(" ")
      : null;

  const medianPoints = data.map((d, i) => `${scaleX(i)},${scaleY(d.median)}`).join(" ");

  return (
    <div className="relative w-full" style={{ maxWidth: width }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        style={{ overflow: "visible" }}
        onMouseLeave={() => setTooltip(null)}
      >
        {/* Uncertainty band */}
        {bandPoints && <polygon points={bandPoints} fill={color} fillOpacity="0.15" />}
        {singlePointBand && (
          <g stroke={color} strokeWidth="2" strokeOpacity="0.5">
            <line
              x1={scaleX(0)}
              y1={scaleY(singlePointBand.low as number)}
              x2={scaleX(0)}
              y2={scaleY(singlePointBand.high as number)}
            />
            <line
              x1={scaleX(0) - 5}
              y1={scaleY(singlePointBand.low as number)}
              x2={scaleX(0) + 5}
              y2={scaleY(singlePointBand.low as number)}
            />
            <line
              x1={scaleX(0) - 5}
              y1={scaleY(singlePointBand.high as number)}
              x2={scaleX(0) + 5}
              y2={scaleY(singlePointBand.high as number)}
            />
          </g>
        )}

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

        {/* Median line */}
        <polyline
          points={medianPoints}
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
            cy={scaleY(d.median)}
            r={tooltip?.point === d ? 5 : 3}
            fill={color}
            stroke="hsl(var(--background))"
            strokeWidth="1.5"
            style={{ cursor: "pointer" }}
            onMouseEnter={() => setTooltip({ x: scaleX(i), y: scaleY(d.median), point: d })}
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
              x={Math.min(tooltip.x + 6, width - 130)}
              y={Math.max(tooltip.y - 36, 0)}
              width={124}
              height={tooltip.point.low !== null && tooltip.point.high !== null ? 36 : 22}
              rx="4"
              fill="hsl(var(--card))"
              stroke="hsl(var(--border))"
            />
            <text
              x={Math.min(tooltip.x + 10, width - 126)}
              y={Math.max(tooltip.y - 36, 0) + 14}
              fontSize="10"
              fill="hsl(var(--foreground))"
            >
              {tooltip.point.time}: {formatValue(tooltip.point.median)}
              {unit ? ` ${unit}` : ""}
            </text>
            {tooltip.point.low !== null && tooltip.point.high !== null && (
              <text
                x={Math.min(tooltip.x + 10, width - 126)}
                y={Math.max(tooltip.y - 36, 0) + 28}
                fontSize="10"
                fill="hsl(var(--muted-foreground))"
              >
                band {formatValue(tooltip.point.low)} – {formatValue(tooltip.point.high)}
              </text>
            )}
          </g>
        )}
      </svg>
    </div>
  );
}
