"use client";

import { useId } from "react";
import { TOUCH_TARGET } from "@/components/ui/touch-target";
import { cn } from "@/lib/utils";
import {
  useInterventionVisualizationStore,
  type InterventionVisualizationMode,
} from "@/components/map/visualization/intervention-visualization-store";

const MODE_OPTIONS: ReadonlyArray<{ value: InterventionVisualizationMode; label: string }> = [
  { value: "off", label: "Off" },
  { value: "heatmap", label: "Heatmap" },
  { value: "points", label: "Points" },
  { value: "flow", label: "Flow" },
];

/**
 * off/heatmap/points/flow radio group for the `interventions` row's extra rendering. Mounted only
 * while that row's own eye is on -- see `LayerRow`'s `isActive` gate -- matching the opacity and
 * time sliders beside it, which likewise appear "only for a layer that is actually on" (a control
 * that adjusts nothing withheld is the fabricated affordance `layer-legends.ts` exists to
 * prevent).
 *
 * Writes `useInterventionVisualizationStore` only; the map-side renderer
 * (`InterventionVisualizationLayer`) reads the same store independently, since the two live in
 * different parts of the tree (this control inside the sidebar dock, the renderer always-mounted
 * beside `LayerManager`) -- see src/components/map/AGENTS.md "Interventions visualization modes".
 */
export function InterventionVisualizationModeControl() {
  const mode = useInterventionVisualizationStore((state) => state.mode);
  const setMode = useInterventionVisualizationStore((state) => state.setMode);
  const descriptionId = useId();

  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
        Extra rendering
      </span>
      <div
        role="radiogroup"
        aria-describedby={descriptionId}
        className="flex flex-wrap items-center gap-1"
      >
        <span id={descriptionId} className="sr-only">
          Choose an extra rendering for the interventions layer: off, heatmap, points or flow.
        </span>
        {MODE_OPTIONS.map((option) => {
          const isSelected = mode === option.value;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={isSelected}
              data-testid={`intervention-visualization-mode-${option.value}`}
              onClick={() => setMode(option.value)}
              className={cn(
                TOUCH_TARGET,
                "rounded-(--radius) border px-1.5 py-0.5 text-[10px] transition-colors",
                isSelected
                  ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.12)] text-[hsl(var(--foreground))]"
                  : "border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"
              )}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
