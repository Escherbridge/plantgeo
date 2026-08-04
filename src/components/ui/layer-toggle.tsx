"use client";

import { useMapStore } from "@/stores/map-store";

interface LayerToggleProps {
  layerId: string;
  label: string;
  /** When set, the switch is disabled and this reads as a caption beneath it. */
  unavailableReason?: string;
}

export function LayerToggle({ layerId, label, unavailableReason }: LayerToggleProps) {
  const activeLayers = useMapStore((s) => s.activeLayers);
  const toggleLayer = useMapStore((s) => s.toggleLayer);
  const isUnavailable = Boolean(unavailableReason);
  const isActive = !isUnavailable && activeLayers.includes(layerId);

  return (
    <div className="flex flex-col gap-1 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted)/0.3)] px-3 py-2 select-none">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-[hsl(var(--foreground))]">
          Show {label} on map
        </span>
        <button
          type="button"
          role="switch"
          aria-label={`Show ${label} on map`}
          aria-checked={isActive}
          aria-disabled={isUnavailable ? "true" : undefined}
          disabled={isUnavailable}
          onClick={() => toggleLayer(layerId)}
          className={[
            "inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]",
            isUnavailable ? "cursor-not-allowed opacity-50" : "",
          ].join(" ")}
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
      {unavailableReason && (
        <p className="text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]">
          {unavailableReason}
        </p>
      )}
    </div>
  );
}
