"use client";

import { useEffect, useState } from "react";

export interface TooltipInfo {
  x: number;
  y: number;
  object: Record<string, unknown> | null;
}

interface DeckTooltipProps {
  info: TooltipInfo | null;
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export default function DeckTooltip({ info }: DeckTooltipProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    setVisible(!!info?.object);
  }, [info]);

  if (!visible || !info?.object) return null;

  const entries = Object.entries(info.object).filter(
    ([k]) => !["coordinates", "path", "timestamps"].includes(k)
  );

  return (
    <div
      className="pointer-events-none absolute z-50 max-w-xs rounded-lg border border-white/10 bg-slate-900/80 px-3 py-2 shadow-xl backdrop-blur-md"
      style={{ left: info.x + 12, top: info.y - 12 }}
    >
      {entries.length === 0 ? (
        <p className="text-xs text-slate-300">No properties</p>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
          {entries.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="truncate text-xs font-medium text-slate-400">
                {key}
              </dt>
              <dd className="truncate text-xs text-slate-100">
                {formatValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
