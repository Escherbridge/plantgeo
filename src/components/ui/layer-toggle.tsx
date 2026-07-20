"use client";

import { useMapStore } from "@/stores/map-store";

interface LayerToggleProps {
  layerId: string;
  label: string;
}

export function LayerToggle({ layerId, label }: LayerToggleProps) {
  const activeLayers = useMapStore((s) => s.activeLayers);
  const toggleLayer = useMapStore((s) => s.toggleLayer);
  const isActive = activeLayers.includes(layerId);

  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted)/0.3)] px-3 py-2 select-none">
      <span className="text-xs font-medium text-[hsl(var(--foreground))]">
        Show {label} on map
      </span>
      <button
        type="button"
        role="switch"
        aria-label={`Show ${label} on map`}
        aria-checked={isActive}
        onClick={() => toggleLayer(layerId)}
        className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
      >
        <span
          aria-hidden="true"
          className={[
            "relative inline-flex h-5 w-9 rounded-full transition-colors",
            isActive ? "bg-emerald-500" : "bg-[hsl(var(--muted))]",
          ].join(" ")}
        >
          <span
            className={[
              "pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
              isActive ? "translate-x-4" : "translate-x-0.5",
              "mt-0.5",
            ].join(" ")}
          />
        </span>
      </button>
    </div>
  );
}
