"use client";

import type { LayerLegendSpec } from "@/lib/map/layer-legends";
import type { LayerToggleId } from "@/lib/map/layer-registry";

/**
 * The 12px chip in a layer row's gutter: what this layer looks like on the map, at a glance.
 *
 * Read entirely off `layerLegendSpec()` -- the module that already reads every colour from the
 * renderer that paints it -- so a chip can never show a colour the map does not draw. A layer
 * with no spec gets a hatched chip rather than a plausible grey: `layer-legends.ts` records
 * (in LEGENDLESS_TOGGLE_REASONS) that `soil` and `building-footprints` paint nothing at all
 * today, and inventing a colour for them is exactly the drift that module exists to prevent.
 */

/** Evenly spaced stops, matching how Legend.tsx draws a ramp bar. */
function rampGradient(colors: readonly string[]): string {
  if (colors.length < 2) return colors[0] ?? "transparent";
  const lastIndex = colors.length - 1;
  return `linear-gradient(to right, ${colors
    .map((color, index) => `${color} ${((index / lastIndex) * 100).toFixed(2)}%`)
    .join(", ")})`;
}

/** Diagonal hatching: reads as "no encoding", which is the honest answer, not as a colour. */
const HATCHED =
  "repeating-linear-gradient(45deg, hsl(var(--muted-foreground) / 0.35) 0 2px, transparent 2px 4px)";

interface SwatchPaint {
  backgroundColor?: string;
  backgroundImage?: string;
  borderColor?: string;
}

/**
 * The first block that carries a colour decides the chip. `note` blocks are prose and are
 * skipped; a spec made only of notes falls through to the hatch.
 */
function swatchPaintFor(spec: LayerLegendSpec | null): SwatchPaint {
  for (const block of spec?.blocks ?? []) {
    if (block.kind === "swatch") {
      return { backgroundColor: block.fillColor, borderColor: block.outlineColor };
    }
    if (block.kind === "classes" && block.classes.length > 0) {
      return { backgroundImage: rampGradient(block.classes.map((entry) => entry.color)) };
    }
    if (block.kind === "ramp" && block.stops.length > 0) {
      return { backgroundImage: rampGradient(block.stops.map((stop) => stop.color)) };
    }
  }
  return { backgroundImage: HATCHED };
}

/**
 * Toggles whose features are points, so their chip reads as a dot rather than an area.
 *
 * Declared here rather than at each call site: the layer tree's rows and the legend's chips
 * both draw this chip, and a set copied into both is a set that drifts in one of them.
 */
const POINT_LAYER_TOGGLE_IDS: ReadonlySet<LayerToggleId> = new Set<LayerToggleId>([
  "fire",
  "water",
  "weather",
  "sensors",
  "demand-heatmap",
]);

export function LayerSwatch({
  layerId,
  spec,
}: {
  layerId: LayerToggleId;
  spec: LayerLegendSpec | null;
}) {
  const paint = swatchPaintFor(spec);
  const isRound = POINT_LAYER_TOGGLE_IDS.has(layerId);
  return (
    <span
      aria-hidden="true"
      data-testid="layer-swatch"
      className={`h-3 w-3 shrink-0 border border-black/10 ${isRound ? "rounded-full" : "rounded-sm"}`}
      style={{
        backgroundColor: paint.backgroundColor,
        backgroundImage: paint.backgroundImage,
        borderColor: paint.borderColor,
      }}
    />
  );
}
