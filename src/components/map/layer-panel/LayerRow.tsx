"use client";

import { useId } from "react";
import { Eye, EyeOff } from "lucide-react";
import { LayerIcon } from "@/components/map/layer-panel/layer-icons";
import { LayerSwatch } from "@/components/map/layer-panel/LayerSwatch";
import { LayerTimeSlider } from "@/components/map/layer-panel/LayerTimeSlider";
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
import { hasSelectableDay, useTimeSliderStore } from "@/stores/time-slider-store";

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
 * The eye and the two sliders are INDEPENDENT controls of different things. The eye sets layout
 * visibility (or mounts/unmounts the component), which is what removes a layer from
 * `queryRenderedFeatures`; the opacity slider only scales how strongly it paints and is floored
 * above zero, because a fully transparent layer still swallows clicks meant for the ground
 * beneath it (see src/lib/map/layer-opacity.ts); the time slider moves this layer's own day and
 * nothing else's.
 *
 * The time slider is per ROW because the map is a mixed-time composite since 2026-08-09: fire on
 * 2026-08-07 beside vegetation on 2025-06-14 looks like one moment in a screenshot, so the day
 * has to be stated on the row that owns it rather than once for the whole map. This row decides
 * WHETHER a layer gets that control; `LayerTimeSlider` owns everything the control then says,
 * including the mark for a layer sitting behind its own newest published day.
 */
export function LayerRow({ layerId, legendContext }: LayerRowProps) {
  const entry = LAYER_REGISTRY[layerId];
  const isToggledOn = useLayerToggle(layerId);
  const toggleLayer = useToggleLayer();
  const opacity = useLayerOpacity(layerId);
  // Advisory only, and only worth saying while the layer is on: a layer that is drawing
  // nothing because the warehouse has no rows for this layer's day is indistinguishable from
  // one the reader switched off, which is the confusion this caption exists to prevent.
  const { unavailableReason, warehouseLayerName } = useLayerRenderState(layerId);

  // Whether this row mounts a time control at all, and the answer is deliberately THREE-valued
  // collapsed to two.
  //
  // `hasSelectableDay` is the contract's single rule for "this layer is date-filtered, so it
  // must have a control" -- it already refuses a toggle no warehouse stream backs, a stream this
  // payload does not carry, a SNAPSHOT (watersheds' 9,396 basins share one 2013 WBD loaddate, so
  // a track over them would advertise years of scrubbing across an unchanging boundary set), and
  // a stream that has observed nothing. Mounting a control for those is the fabricated affordance
  // layer-legends.ts exists to prevent.
  //
  // But with NO capabilities that function cannot answer, and it returns false -- which is not
  // "this layer has no dates", it is "nobody knows yet". Treating the two alike is what stripped
  // the time control from every row during the read model's bigint 500 with nothing anywhere
  // saying why, so the unknown mounts the control and lets it state the outage. The registry
  // name is the one thing knowable without the payload, and it keeps the eight layers that never
  // had a warehouse stream out of it.
  //
  // Selected as a BOOLEAN, so the five-minute capabilities poll re-renders this row only when
  // the answer actually changes rather than on every fresh payload object.
  //
  // `streamsUnavailable` joins `capabilities === null` on the unknown side for the same reason.
  // A payload whose stream scan timed out carries only the geo.features layers, so every
  // stream-backed layer reads as "no selectable day" -- indistinguishable from a layer that
  // genuinely has no history, and the exact shape of the outage above: the sliders vanished
  // from drought, the three soil measures and the nine climate signals with nothing saying why.
  const mountsTimeSlider = useTimeSliderStore(
    (state) =>
      warehouseLayerName !== null &&
      (state.capabilities === null ||
        state.capabilities.streamsUnavailable ||
        hasSelectableDay(state.capabilities, layerId))
  );

  const withheldReason = entry.permanentlyUnavailableReason;
  const isWithheld = withheldReason !== null;
  const isActive = !isWithheld && isToggledOn;
  const legendlessReason = LEGENDLESS_TOGGLE_REASONS[layerId] ?? null;
  const spec = layerLegendSpec(layerId, legendContext);

  // Links every caption under this row to the eye, so a screen reader landing on a disabled or
  // empty layer hears why rather than just "switch, off".
  //
  // The "not on its own latest day" mark is deliberately NOT here. `LayerTimeSlider` states it,
  // in words, inside the control that moved the day -- and stating it twice one line apart is
  // the same defect the dock already recorded once, when `TimeSlider` kept an `<h2>Map date</h2>`
  // directly under a section header saying "Map date".
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

        <LayerSwatch layerId={layerId} spec={spec} />

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

      {/* Both sliders appear only for a layer that is actually on: a control that adjusts
          nothing is the fabricated affordance layer-legends.ts exists to prevent. A layer that
          is on but paints nothing (soil, whose tile template is still empty) gets the opacity
          control disabled with the reason, rather than a live slider over an absent raster.

          The time slider is additionally gated on the layer having a day to select -- or on
          that being unknown -- so a snapshot and a layer with no warehouse feed behind it get no
          track rather than a dead one, while a failed capabilities load still reaches a control
          that can say so; see `mountsTimeSlider`. Stacked under the opacity slider rather than
          set beside it: the dock column is 19rem, and splitting it would leave each track under
          7rem, where a four-year axis cannot address a day at all. */}
      {isActive && (
        <div className="flex flex-col gap-1 pl-[3.5rem] pr-1">
          <LayerOpacitySlider
            layerId={layerId}
            showCaption={false}
            inertReason={legendlessReason}
          />
          {/* Wrapped so the decision this row makes -- does this layer get a time control at
              all -- is observable without reaching into the slider's own markup. The gate is the
              contract here; what the control then says, axis or outage, belongs to
              `LayerTimeSlider`. */}
          {mountsTimeSlider && (
            <div data-testid={`layer-time-slider-slot-${layerId}`}>
              <LayerTimeSlider layerId={layerId} />
            </div>
          )}
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
