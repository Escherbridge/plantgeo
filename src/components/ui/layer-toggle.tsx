"use client";

import { useId } from "react";
import { LAYER_REGISTRY, layerLabel, type LayerToggleId } from "@/lib/map/layer-registry";
import { useLayerToggle, useToggleLayer } from "@/lib/map/layer-toggle-context";

interface LayerToggleProps {
  layerId: LayerToggleId;
  /**
   * Overrides the registry's own name. Defaults to it, which is the point: this prop was
   * required until 2026-08-08 and sixteen call sites each hand-typed the string, so a layer
   * tree grouped by category had nowhere to read a layer's name from.
   */
  label?: string;
  /**
   * Overrides the registry's own reason. When either is set the switch is disabled and the
   * reason reads as a caption beneath it, so a withheld capability is visible, not missing.
   */
  unavailableReason?: string;
}

export function LayerToggle({ layerId, label, unavailableReason }: LayerToggleProps) {
  const resolvedLabel = label ?? layerLabel(layerId);
  const isToggledOn = useLayerToggle(layerId);
  const toggleLayer = useToggleLayer();
  // A layer withheld by governance carries its reason in the registry, so every switch for
  // it is disabled by construction rather than by each panel remembering to pass the prop.
  const reason = unavailableReason ?? LAYER_REGISTRY[layerId].permanentlyUnavailableReason;
  const isUnavailable = reason !== null;
  const isActive = !isUnavailable && isToggledOn;
  // Links the reason caption to the switch: a screen reader landing on a disabled switch
  // otherwise hears only "disabled", never why the capability is withheld.
  const reasonId = useId();

  return (
    <div className="flex flex-col gap-1 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted)/0.3)] px-3 py-2 select-none">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-[hsl(var(--foreground))]">
          Show {resolvedLabel} on map
        </span>
        <button
          type="button"
          role="switch"
          aria-label={`Show ${resolvedLabel} on map`}
          aria-checked={isActive}
          aria-disabled={isUnavailable ? "true" : undefined}
          aria-describedby={isUnavailable ? reasonId : undefined}
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
      {reason !== null && (
        <p id={reasonId} className="text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]">
          {reason}
        </p>
      )}
    </div>
  );
}
