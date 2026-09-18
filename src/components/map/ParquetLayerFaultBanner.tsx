"use client";

/** Renders LayerManager's `parquetLayerFaults` list as stacked alert lines. */

export interface ParquetLayerFault {
  layerId: string;
  tone: "fault" | "notice";
  message: string;
}

export function ParquetLayerFaultBanner({ faults }: { faults: ParquetLayerFault[] }) {
  if (faults.length === 0) return null;
  return (
    <div
      className="pointer-events-none absolute left-1/2 top-12 z-20 flex -translate-x-1/2 flex-col gap-1.5"
      aria-live="assertive"
    >
      {faults.map((fault) => (
        <p
          key={fault.layerId}
          role="alert"
          className={
            fault.tone === "fault"
              ? "rounded-md border border-red-500/40 bg-[hsl(var(--card))]/95 px-3 py-1.5 text-xs font-medium text-red-600 shadow-sm backdrop-blur dark:text-red-400"
              : "rounded-md border border-amber-500/40 bg-[hsl(var(--card))]/95 px-3 py-1.5 text-xs font-medium text-amber-700 shadow-sm backdrop-blur dark:text-amber-400"
          }
          data-testid={`parquet-layer-unavailable-${fault.layerId}`}
        >
          {fault.message}
        </p>
      ))}
    </div>
  );
}
