"use client";

import { useMemo } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useLayerStore } from "@/stores/layer-store";
import { useLayerVisibility, useSoilDisplayMode } from "@/lib/map/layer-toggle-context";
import { useVegetationStore } from "@/stores/vegetation-store";
import {
  activeLegendEntries,
  type LegendBlock,
  type LegendRampStop,
} from "@/lib/map/layer-legends";

/**
 * The map's persistent legend: one section per layer that is switched on and paints
 * something, so an encoding stays readable with every panel closed.
 *
 * It reads the toggle context and `src/lib/map/layer-legends.ts` and nothing else. It used
 * to list `geo.layers` rows from `trpc.layers.list` with one flat swatch each, which named
 * warehouse rows rather than drawn encodings and needed a network round-trip to say
 * anything at all. See src/components/map/AGENTS.md "The legend legends what is drawn".
 */
export function Legend() {
  const legendVisible = useLayerStore((state) => state.legendVisible);
  const toggleLegend = useLayerStore((state) => state.toggleLegend);
  const layerVisibility = useLayerVisibility();
  const soilDisplayMode = useSoilDisplayMode();
  // The two vegetation display fields are read from the store directly rather than through
  // useVegetationDisplayMode: that hook also projects the slider's settled day, which costs
  // a store subscription and a settle timer per mount and moves nothing on this card.
  const vegetationMode = useVegetationStore((state) => state.mode);
  const ndviMode = useVegetationStore((state) => state.ndviMode);

  const entries = useMemo(
    () =>
      activeLegendEntries(layerVisibility, {
        vegetationMode,
        ndviMode,
        soilFieldDepth: soilDisplayMode.fieldDepth,
      }),
    [layerVisibility, vegetationMode, ndviMode, soilDisplayMode.fieldDepth]
  );

  // The map opens with every layer off, so the card earns its corner only once something
  // is drawn -- and a layer that paints nothing contributes no entry either.
  if (entries.length === 0) return null;

  return (
    <section
      aria-label="Map legend"
      data-testid="legend"
      className="absolute bottom-8 right-4 z-10 flex max-h-[min(60vh,24rem)] min-w-40 max-w-[min(18rem,calc(100vw-2rem))] flex-col rounded-(--radius) border border-(--glass-border) bg-(--glass-bg) p-3 shadow-(--shadow-lg) [backdrop-filter:blur(var(--glass-blur))]"
    >
      <div className="flex shrink-0 items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
          Legend
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 max-sm:h-11 max-sm:w-11"
          aria-label={legendVisible ? "Hide legend entries" : "Show legend entries"}
          onClick={toggleLegend}
        >
          {legendVisible ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
        </Button>
      </div>

      {legendVisible && (
        // min-h-0 so this scrolls inside the capped card instead of stretching it: a flex
        // child's default min-height is its content.
        <div className="mt-2 flex min-h-0 flex-col gap-3 overflow-y-auto">
          {entries.map((entry) => (
            <div
              key={entry.toggleId}
              data-testid={`legend-entry-${entry.toggleId}`}
              className="flex flex-col gap-1.5"
            >
              <h3 className="text-xs font-semibold text-[hsl(var(--foreground))]">
                {entry.spec.title}
              </h3>
              {entry.spec.blocks.map((block, blockIndex) => (
                <LegendBlockView key={blockIndex} block={block} />
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * Evenly spaced CSS stops. Position is deliberately not the stop's value -- see the
 * `LegendRampBlock` note in src/lib/map/layer-legends.ts.
 */
function rampGradient(stops: readonly LegendRampStop[]): string {
  if (stops.length < 2) return stops[0]?.color ?? "transparent";
  const lastIndex = stops.length - 1;
  const positioned = stops.map(
    (stop, index) => `${stop.color} ${((index / lastIndex) * 100).toFixed(2)}%`
  );
  return `linear-gradient(to right, ${positioned.join(", ")})`;
}

/** A bar's captions: its two ends, plus its middle stop when that one is captioned. */
function rampCaptions(stops: readonly LegendRampStop[]): string[] {
  const middle = stops.length >= 3 ? stops[Math.floor(stops.length / 2)].label : undefined;
  return [stops[0]?.label, middle, stops[stops.length - 1]?.label].filter(
    (label): label is string => label !== undefined
  );
}

function LegendBlockView({ block }: { block: LegendBlock }) {
  if (block.kind === "note") {
    return (
      <p className="text-[10px] leading-snug text-[hsl(var(--muted-foreground))]">
        {block.text}
      </p>
    );
  }

  if (block.kind === "swatch") {
    return (
      <div className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="h-3 w-3 shrink-0 rounded-sm border"
          style={{
            backgroundColor: block.fillColor ?? "transparent",
            borderColor: block.outlineColor,
          }}
        />
        <span className="text-xs text-[hsl(var(--foreground))]">{block.label}</span>
      </div>
    );
  }

  if (block.kind === "ramp") {
    const captions = rampCaptions(block.stops);
    return (
      <div className="flex flex-col gap-1">
        {block.caption !== undefined && (
          <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
            {block.caption}
          </span>
        )}
        {/* The bar itself carries no accessible meaning; the captions below it do. */}
        <span
          aria-hidden="true"
          className="block h-2 w-full rounded-full"
          style={{ backgroundImage: rampGradient(block.stops) }}
        />
        {captions.length > 0 && (
          <div className="flex justify-between gap-2 text-[10px] text-[hsl(var(--muted-foreground))]">
            {captions.map((caption) => (
              <span key={caption}>{caption}</span>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      {block.caption !== undefined && (
        <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
          {block.caption}
        </span>
      )}
      <ul className="flex flex-col gap-1">
        {block.classes.map((legendClass) => (
          <li key={legendClass.label} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className={`h-3 w-3 shrink-0 border border-black/10 ${
                block.shape === "dot" ? "rounded-full" : "rounded-sm"
              }`}
              style={{ backgroundColor: legendClass.color }}
            />
            <span className="text-xs leading-snug text-[hsl(var(--foreground))]">
              {legendClass.label}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
