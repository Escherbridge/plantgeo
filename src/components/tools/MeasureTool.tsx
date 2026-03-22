"use client";

import React from "react";
import { useMeasurement } from "@/hooks/useMeasurement";
import type { MeasurementUnit } from "@/hooks/useMeasurement";

export function MeasureTool() {
  const { distance, area, unit, setUnit } = useMeasurement();

  if (!distance && !area) return null;

  return (
    <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-10 flex items-center gap-3 rounded-xl px-4 py-2.5 bg-[var(--glass-bg)] border border-[var(--glass-border)] shadow-[var(--shadow-lg)] [backdrop-filter:blur(var(--glass-blur))]">
      {distance && (
        <div className="flex items-center gap-1.5">
          <svg
            className="size-4 text-[hsl(var(--primary))]"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path d="M3 12h18M3 6l3 6-3 6M21 6l-3 6 3 6" />
          </svg>
          <span className="text-sm font-medium text-[hsl(var(--foreground))]">
            {distance}
          </span>
        </div>
      )}

      {distance && area && (
        <div className="w-px h-4 bg-[var(--glass-border)]" />
      )}

      {area && (
        <div className="flex items-center gap-1.5">
          <svg
            className="size-4 text-[hsl(var(--primary))]"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
          >
            <rect x="3" y="3" width="18" height="18" rx="1" />
          </svg>
          <span className="text-sm font-medium text-[hsl(var(--foreground))]">
            {area}
          </span>
        </div>
      )}

      <div className="w-px h-4 bg-[var(--glass-border)]" />

      <select
        className="text-sm bg-transparent text-[hsl(var(--foreground))] outline-none cursor-pointer"
        value={unit}
        onChange={(e) => setUnit(e.target.value as MeasurementUnit)}
      >
        <option value="metric">Metric</option>
        <option value="imperial">Imperial</option>
      </select>
    </div>
  );
}
