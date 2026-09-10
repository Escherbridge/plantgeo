"use client";

import { GIBS_NDVI_PRODUCT, NDVI_COLOR_RAMP } from "@/lib/vegetation";
import { useVegetationStore } from "@/stores/vegetation-store";
import {
  useLayerDay,
  useLayerRenderState,
  useVegetationDisplayMode,
} from "@/lib/map/layer-toggle-context";
import type { VegetationSource } from "@/components/map/layers/VegetationLayer";


/**
 * The two NDVI encodings, named by provenance rather than by style. Each carries the sentence
 * a reader needs to know WHICH one they are looking at -- the pair draws the same quantity, so
 * without the description the choice is arbitrary. Exhaustive over `VegetationSource` by
 * construction: the picker maps this list, so a new member appears the moment it is added.
 */
const VEGETATION_SOURCE_OPTIONS: ReadonlyArray<{
  value: VegetationSource;
  label: string;
  description: string;
}> = [
  {
    value: "measured",
    label: "Measured grid",
    description:
      "The NDVI readings this platform ingested, drawn as the discrete 0.25° cells they were " +
      "sampled on. Present only where the grid has been sampled.",
  },
  {
    value: "satellite",
    label: "Satellite (MODIS)",
    description:
      `NASA GIBS ${GIBS_NDVI_PRODUCT.layerIdentifier.replace(/_/g, " ")}: global and gap-free, ` +
      `but a proxied composite rather than a reading with a scene behind it. GIBS publishes it ` +
      `no deeper than zoom ${GIBS_NDVI_PRODUCT.maxZoom}, so it softens as you zoom past that.`,
  },
];

// The geo.layers name these NDVI controls drive lives in the layer registry
// ("vegetation" -> "vegetation").

/** The NDVI interval a legend class covers: its own stop up to the next one, open at the top. */
function formatNdviBand(lower: number, upper: number | undefined): string {
  return upper === undefined
    ? `${lower.toFixed(1)}+`
    : `${lower.toFixed(1)} – ${upper.toFixed(1)}`;
}

function ColorLegendRow({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-4 h-3 rounded-sm shrink-0" style={{ backgroundColor: color }} />
      <span className="text-[hsl(var(--muted-foreground))] text-xs">{label}</span>
    </div>
  );
}

/**
 * What the vegetation layer is drawing and in which encoding, as the Vegetation section of the
 * map dock. Mounted only while that section is expanded; the `<LayerToggle>` this panel
 * carried is gone, since the section's own layer row is the switch.
 */
export function VegetationDetails() {
  const vegStore = useVegetationStore();
  const source = vegStore.source;
  const activeSource = VEGETATION_SOURCE_OPTIONS.find((option) => option.value === source);
  // Everything the GIBS composite implies -- a monthly period, a gap where GIBS publishes
  // nothing -- is true only while the composite is the encoding on screen. The measured cells
  // take the slider's day directly, so saying "NDVI draws the Aug 2026 composite" over them
  // would describe a raster the reader is not looking at.
  const showsComposite = source === "satellite";

  // The `vegetation` row's own day, read from the toggle context. This panel owns NO time
  // control of its own: the slider on that row is this layer's one clock, and the composite
  // below is that day projected onto a month. `useLayerDay` for the readout so it tracks the
  // pointer; the composite comes from the settled day the raster actually drew.
  //
  // Keyed to `vegetation` and nothing wider. Every other layer is on its own day now, so a
  // readout built from any of them would caption this raster with a day nobody selected for it.
  const selectedDate = useLayerDay("vegetation").selectedDate;
  const hasSelectedDay = selectedDate !== null;
  const { compositePeriod, compositeUnavailableReason } = useVegetationDisplayMode();

  const vegetationReason = useLayerRenderState("vegetation").unavailableReason;

  return (
    <div className="flex flex-col">
      {/* This section has no time control of its own -- the slider on the Vegetation layer row
          is this layer's one clock. What it does owe the reader is what that day means HERE:
          GIBS publishes NDVI as an 8-day composite binned by month, so every day in a month
          draws the same tile. Stating the period keeps a day-granular scrub from reading as a
          day-granular raster.

          "Vegetation date", not "Map date": the map has no single date since 2026-08-09, and a
          caption inside the Vegetation section claiming one would be read as covering every
          other layer on screen -- which are each on a day of their own. */}
      {hasSelectedDay && (
        <p
          className="mt-3 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-2 py-1.5 text-[11px] text-[hsl(var(--muted-foreground))]"
          data-testid="vegetation-composite-period"
        >
          Vegetation date{" "}
          <span className="font-medium text-[hsl(var(--foreground))]">{selectedDate}</span>
          {/* Only claims a composite is drawn when one exists AND is the selected encoding --
              the gap notice below owns the other case, and saying "NDVI draws Jul 2024" for a
              period GIBS never published would be exactly the silent substitution this
              readout prevents. */}
          {showsComposite && compositePeriod !== null && compositeUnavailableReason === null && (
            <>
              {" — NDVI draws the "}
              <span className="font-medium text-[hsl(var(--foreground))]">
                {compositePeriod}
              </span>
              {" composite; the period follows this layer's date, month by month."}
            </>
          )}
          {!showsComposite && " — the measured cells are the readings sampled up to that day."}
        </p>
      )}

      {/* Outside the tabs on purpose: the day is outside what GIBS publishes, so the raster
          is genuinely absent rather than switched off or still loading, and that must be
          legible the moment the panel opens rather than only on the NDVI tab. On the page,
          not in a title -- there is no focusable control here to hang one on. Gated on the
          composite being the selected encoding: it is a gap in GIBS, not in the measured
          cells, so over the grid it would warn about a raster nobody asked for. */}
      {showsComposite && compositeUnavailableReason !== null && (
        <p
          className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
          data-testid="vegetation-composite-unavailable"
        >
          {compositeUnavailableReason}
        </p>
      )}

      {/* No scroller of its own: the dock's body is the one scrolling element, and a second
          one nested inside it is the defect panel-scroll.ts rule 2 exists to prevent. */}
      <div className="mt-4">
        <div className="flex flex-col gap-4">
            {/* Why the vegetation layer has nothing for the selected day, so an empty
                layer is never mistaken for the toggle being off. */}
            {vegetationReason !== null && (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                {vegetationReason}
              </p>
            )}

            {/* The one real choice this tab offers. NDVI is drawn EITHER as the measured
                cells OR as the GIBS composite -- both used to paint at once, stacked at the
                same alpha, which is what made the layer look broken. Radio-style buttons,
                not checkboxes: they are two views of one quantity, so "both off" would be
                the toggle above, and "both on" is the bug. */}
            <div className="flex flex-col gap-2">
              <p className="text-xs text-[hsl(var(--muted-foreground))]">NDVI source</p>
              <div className="flex gap-2" role="group" aria-label="NDVI source">
                {VEGETATION_SOURCE_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    aria-pressed={source === option.value}
                    data-testid={`vegetation-source-${option.value}`}
                    className={`flex-1 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                      source === option.value
                        ? "bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] border-transparent"
                        : "border-[hsl(var(--border))] text-[hsl(var(--foreground))] bg-[hsl(var(--card))]"
                    }`}
                    onClick={() => vegStore.setSource(option.value)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <p className="text-[10px] text-[hsl(var(--muted-foreground))] leading-relaxed">
                {activeSource?.description}
              </p>
            </div>



            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
              <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                NDVI Legend
              </p>
              {/* Each class carries the NDVI interval it names. The labels alone ("Sparse",
                  "Moderate") are a vocabulary, not a definition -- two readers will not draw
                  the same line between them, and NDVI is a measured index with an actual
                  number behind every band. `value` on each stop is the band's lower bound, so
                  a band runs to the next stop's value and the last runs to the index maximum. */}
              <div className="flex flex-col gap-1">
                {NDVI_COLOR_RAMP.map((stop, index) => (
                  <ColorLegendRow
                    key={stop.color}
                    color={stop.color}
                    label={`${formatNdviBand(stop.value, NDVI_COLOR_RAMP[index + 1]?.value)} · ${stop.label}`}
                  />
                ))}
              </div>
              {/* The composite arrives already shaded by GIBS's server-side palette, so this
                  ramp describes the cells and only approximates the raster. Saying so is
                  cheaper than implying the two are the same scale. */}
              {source === "satellite" && (
                <p className="mt-2 text-[10px] text-[hsl(var(--muted-foreground))] leading-relaxed">
                  The MODIS composite is shaded by NASA GIBS&apos;s own palette; this ramp
                  describes the measured cells and only approximates the raster.
                </p>
              )}
            </div>

        </div>
      </div>
    </div>
  );
}
