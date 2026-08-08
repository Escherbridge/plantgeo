"use client";

import { useId } from "react";
import { Eye, EyeOff } from "lucide-react";
import { LayerIcon } from "@/components/map/layer-panel/layer-icons";
import { LayerSwatch } from "@/components/map/layer-panel/LayerSwatch";
import { LayerOpacitySlider } from "@/components/ui/layer-opacity-slider";
import {
  layerLegendSpec,
  LEGENDLESS_TOGGLE_REASONS,
  type LegendContext,
} from "@/lib/map/layer-legends";
import { LAYER_REGISTRY, type LayerToggleId } from "@/lib/map/layer-registry";
import {
  useLayerOpacity,
  useLayerRenderState,
  useLayerToggle,
  useToggleLayer,
} from "@/lib/map/layer-toggle-context";

/** Toggles whose features are points, so their chip should read as a dot rather than an area. */
const POINT_LAYER_TOGGLE_IDS: ReadonlySet<LayerToggleId> = new Set<LayerToggleId>([
  "fire",
  "water",
  "weather",
  "sensors",
  "demand-heatmap",
]);

interface LayerRowProps {
  layerId: LayerToggleId;
  /** The display modes that decide what this layer paints, and so what its chip shows. */
  legendContext: LegendContext;
}

/**
 * One layer: eye, chip, name, strength.
 *
 * The eye writes `map-store.activeLayers` through the same `useToggleLayer` the sheets'
 * switches use, so the tree and the sheets are two views of one value and cannot disagree --
 * that single source is the rule in src/components/map/AGENTS.md.
 *
 * The eye and the slider are INDEPENDENT controls of different things. The eye sets layout
 * visibility (or mounts/unmounts the component), which is what removes a layer from
 * `queryRenderedFeatures`; the slider only scales how strongly it paints and is floored above
 * zero, because a fully transparent layer still swallows clicks meant for the ground beneath
 * it. See src/lib/map/layer-opacity.ts.
 */
export function LayerRow({ layerId, legendContext }: LayerRowProps) {
  const entry = LAYER_REGISTRY[layerId];
  const isToggledOn = useLayerToggle(layerId);
  const toggleLayer = useToggleLayer();
  const opacity = useLayerOpacity(layerId);
  // Advisory only, and only worth saying while the layer is on: a layer that is drawing
  // nothing because the warehouse has no rows for the selected day is indistinguishable from
  // one the reader switched off, which is the confusion this caption exists to prevent.
  const { unavailableReason } = useLayerRenderState(layerId);

  const withheldReason = entry.permanentlyUnavailableReason;
  const isWithheld = withheldReason !== null;
  const isActive = !isWithheld && isToggledOn;
  const legendlessReason = LEGENDLESS_TOGGLE_REASONS[layerId] ?? null;
  const spec = layerLegendSpec(layerId, legendContext);

  // Links every caption under this row to the eye, so a screen reader landing on a disabled or
  // empty layer hears why rather than just "switch, off".
  const captionId = useId();
  const captions = [
    isWithheld ? withheldReason : null,
    isActive ? unavailableReason : null,
  ].filter((caption): caption is string => caption !== null);

  return (
    <li
      data-testid={`layer-row-${layerId}`}
      className="flex flex-col gap-1 rounded-md px-1 py-1 hover:bg-[hsl(var(--muted)/0.4)]"
    >
      <div className="grid grid-cols-[2.75rem_0.75rem_1fr_auto] items-center gap-2">
        <button
          type="button"
          role="switch"
          aria-checked={isActive}
          aria-disabled={isWithheld ? "true" : undefined}
          aria-label={`Show ${entry.label} on map`}
          aria-describedby={captions.length > 0 ? captionId : undefined}
          disabled={isWithheld}
          onClick={() => toggleLayer(layerId)}
          // 44px, the tap-target floor the rest of the map chrome holds to.
          className={[
            "flex h-11 w-11 items-center justify-center rounded focus-visible:outline-none",
            "focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]",
            isWithheld
              ? "cursor-not-allowed opacity-50"
              : isActive
                ? "text-[hsl(var(--foreground))]"
                : "text-[hsl(var(--muted-foreground))]",
          ].join(" ")}
        >
          {isActive ? (
            <Eye aria-hidden="true" className="h-4 w-4" />
          ) : (
            <EyeOff aria-hidden="true" className="h-4 w-4" />
          )}
        </button>

        <LayerSwatch spec={spec} isRound={POINT_LAYER_TOGGLE_IDS.has(layerId)} />

        <span
          className={[
            "flex min-w-0 items-center gap-1.5 text-xs",
            isActive
              ? "font-medium text-[hsl(var(--foreground))]"
              : "text-[hsl(var(--muted-foreground))]",
          ].join(" ")}
        >
          <LayerIcon name={entry.icon} className="h-3 w-3 shrink-0 opacity-70" />
          <span className="truncate" title={entry.label}>
            {entry.label}
          </span>
        </span>

        {/* Dimmed at full strength: 100% is the default, so calling it out every row would be
            noise. Anything below it is a deliberate change and reads as one. */}
        <span
          aria-hidden="true"
          className={[
            "text-[10px] tabular-nums",
            isActive && opacity < 1
              ? "text-[hsl(var(--foreground))]"
              : "text-[hsl(var(--muted-foreground))] opacity-50",
          ].join(" ")}
        >
          {Math.round(opacity * 100)}%
        </span>
      </div>

      {/* The slider appears only for a layer that is actually on: a control that adjusts
          nothing is the fabricated affordance layer-legends.ts exists to prevent. A layer that
          is on but paints nothing (soil, whose tile template is still empty) gets the control
          disabled with the reason, rather than a live slider over an absent raster. */}
      {isActive && (
        <div className="pl-[3.5rem] pr-1">
          <LayerOpacitySlider
            layerId={layerId}
            showCaption={false}
            inertReason={legendlessReason}
          />
        </div>
      )}

      {captions.length > 0 && (
        <p
          id={captionId}
          className="pl-[3.5rem] pr-1 text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]"
        >
          {captions.join(" ")}
        </p>
      )}
    </li>
  );
}
