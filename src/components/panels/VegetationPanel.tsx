"use client";

import { useState } from "react";
import { Leaf, Layers, TreePine } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { trpc } from "@/lib/trpc/client";
import {
  ENVIRONMENTAL_TILES_CONFIGURED,
  GIBS_NDVI_PRODUCT,
  NDVI_ANOMALY_UNAVAILABLE_REASON,
  NDVI_COLOR_RAMP,
  NDWI_UNAVAILABLE_REASON,
} from "@/lib/vegetation";
import { NLCD_CATEGORY_CLASSES, NLCD_CLASSES, type NLCDCategory } from "@/lib/environmental/nlcd";
import { useVegetationStore } from "@/stores/vegetation-store";
import {
  useLayerRenderState,
  useMapDay,
  useVegetationDisplayMode,
} from "@/lib/map/layer-toggle-context";
import { LayerToggle } from "@/components/ui/layer-toggle";
import { LayerOpacitySlider } from "@/components/ui/layer-opacity-slider";
import type { VegetationSource } from "@/components/map/layers/VegetationLayer";
import type { LandCoverMode } from "@/components/map/layers/LandCoverLayer";

const ALL_CATEGORIES = Object.keys(NLCD_CATEGORY_CLASSES) as NLCDCategory[];

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

interface VegetationPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bbox?: string;
  // Callbacks to drive the map layers
  onLandCoverModeChange?: (mode: LandCoverMode) => void;
  onEnabledCategoriesChange?: (cats: NLCDCategory[]) => void;
}

// The geo.layers name these NDVI controls drive lives in the layer registry
// ("vegetation" -> "vegetation").

function ColorLegendRow({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-4 h-3 rounded-sm shrink-0" style={{ backgroundColor: color }} />
      <span className="text-[hsl(var(--muted-foreground))] text-xs">{label}</span>
    </div>
  );
}

export function VegetationPanel({
  open,
  onOpenChange,
  bbox,
  onLandCoverModeChange,
  onEnabledCategoriesChange,
}: VegetationPanelProps) {
  const vegStore = useVegetationStore();
  const source = vegStore.source;
  const activeSource = VEGETATION_SOURCE_OPTIONS.find((option) => option.value === source);
  // Everything the GIBS composite implies -- a monthly period, a gap where GIBS publishes
  // nothing -- is true only while the composite is the encoding on screen. The measured cells
  // take the slider's day directly, so saying "NDVI draws the Aug 2026 composite" over them
  // would describe a raster the reader is not looking at.
  const showsComposite = source === "satellite";
  const [landCoverMode, setLandCoverMode] = useState<LandCoverMode>("2021");
  const [enabledCategories, setEnabledCategories] = useState<NLCDCategory[]>([...ALL_CATEGORIES]);

  // The map's day, read from the toggle context. This panel owns NO time control of its own:
  // the time slider at the top of the right-hand region is the one clock for every layer, and
  // the composite below is that day projected onto a month. `useMapDay` for the readout so it
  // tracks the pointer; the composite comes from the settled day the raster actually drew.
  const selectedDate = useMapDay().selectedDate;
  const hasSelectedDay = selectedDate !== null;
  const { compositePeriod, compositeUnavailableReason } = useVegetationDisplayMode();

  const vegetationReason = useLayerRenderState("vegetation").unavailableReason;

  const zonesQuery = trpc.environmental.getReforestationZones.useQuery(
    { bbox: bbox ?? "" },
    { enabled: open && !!bbox }
  );

  const zones = zonesQuery.data?.features ?? [];
  const zonesUnavailable = zonesQuery.data?.availability === "unavailable";
  const zonesFailed = zonesQuery.isError;
  const highCount = zones.filter(
    (f) => (f.properties as Record<string, unknown>).suitability === "High"
  ).length;
  const mediumCount = zones.filter(
    (f) => (f.properties as Record<string, unknown>).suitability === "Medium"
  ).length;
  const lowCount = zones.filter(
    (f) => (f.properties as Record<string, unknown>).suitability === "Low"
  ).length;

  function handleLandCoverModeChange(mode: LandCoverMode) {
    setLandCoverMode(mode);
    onLandCoverModeChange?.(mode);
  }

  function handleToggleCategory(cat: NLCDCategory) {
    setEnabledCategories((prev) => {
      const next = prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat];
      onEnabledCategoriesChange?.(next);
      return next;
    });
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" onOpenChange={onOpenChange}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Leaf className="h-5 w-5 text-green-600" />
            Vegetation & Land Cover
          </SheetTitle>
        </SheetHeader>

        <LayerToggle layerId="vegetation" />

        {/* This panel has no time control of its own -- the slider at the top of the
            right-hand region is the one clock for every layer. What it does owe the reader is
            what that day means HERE: GIBS publishes NDVI as an 8-day composite binned by
            month, so every day in a month draws the same tile. Stating the period keeps a
            day-granular scrub from reading as a day-granular raster. */}
        {hasSelectedDay && (
          <p
            className="mt-3 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-2 py-1.5 text-[11px] text-[hsl(var(--muted-foreground))]"
            data-testid="vegetation-composite-period"
          >
            Map date{" "}
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
                {" composite; the period follows the map date, month by month."}
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

        <div className="mt-4 overflow-y-auto max-h-[calc(100vh-8rem)]">
          <Tabs defaultValue="ndvi">
            <TabsList className="w-full">
              <TabsTrigger value="ndvi" className="flex-1 text-xs">
                <Leaf className="h-3.5 w-3.5 mr-1" />
                NDVI
              </TabsTrigger>
              <TabsTrigger value="landcover" className="flex-1 text-xs">
                <Layers className="h-3.5 w-3.5 mr-1" />
                Land Cover
              </TabsTrigger>
              <TabsTrigger value="reforestation" className="flex-1 text-xs">
                <TreePine className="h-3.5 w-3.5 mr-1" />
                Reforestation
              </TabsTrigger>
            </TabsList>

            {/* NDVI Tab */}
            <TabsContent value="ndvi" className="flex flex-col gap-4 mt-4">
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

              {/* With one encoding drawn at a time the basemap underneath still needs to be
                  readable through it. The same control the layer tree renders, over the same
                  `layer-store.layerOpacity.vegetation` value -- two surfaces, one number, so
                  they cannot disagree. It used to write `vegetation-store.opacity`, which no
                  other layer could reach and no other surface could read. */}
              <div className="flex flex-col gap-2">
                <span className="text-xs text-[hsl(var(--foreground))]">Layer opacity</span>
                <LayerOpacitySlider layerId="vegetation" />
              </div>

              <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                  NDVI Legend
                </p>
                <div className="flex flex-col gap-1">
                  {NDVI_COLOR_RAMP.map((stop) => (
                    <ColorLegendRow key={stop.color} color={stop.color} label={stop.label} />
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

              {/* Stated, not offered. These were checkboxes wired to store setters and
                  permanently `disabled`, so they could never fire -- a control that cannot act
                  is worse than a sentence that explains why. Same wording pattern as the
                  registry's permanentlyUnavailableReason. */}
              <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-2">
                <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                  No data source yet
                </p>
                <p className="text-[10px] text-[hsl(var(--muted-foreground))] leading-relaxed">
                  <span className="font-medium text-[hsl(var(--foreground))]">Anomaly mode</span>
                  {" — "}
                  {NDVI_ANOMALY_UNAVAILABLE_REASON}
                </p>
                <p className="text-[10px] text-[hsl(var(--muted-foreground))] leading-relaxed">
                  <span className="font-medium text-[hsl(var(--foreground))]">
                    NDWI (water stress)
                  </span>
                  {" — "}
                  {NDWI_UNAVAILABLE_REASON}
                </p>
              </div>
            </TabsContent>

            {/* Land Cover Tab */}
            <TabsContent value="landcover" className="flex flex-col gap-4 mt-4">
              {/* This notice belongs here, not on the NDVI tab: NDVI is served -- either from
                  the warehouse cells or the GIBS proxy -- while land cover is exactly what
                  ENVIRONMENTAL_TILES_CONFIGURED gates, so on the NDVI tab it contradicted a
                  layer the reader could plainly see drawing. */}
              {!ENVIRONMENTAL_TILES_CONFIGURED && (
                <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                  Land-cover tiles are paused until a versioned first-party warehouse release
                  is published; the controls below change nothing yet.
                </p>
              )}

              <div className="flex gap-2">
                <button
                  className={`flex-1 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                    landCoverMode === "2021"
                      ? "bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] border-transparent"
                      : "border-[hsl(var(--border))] text-[hsl(var(--foreground))] bg-[hsl(var(--card))]"
                  }`}
                  onClick={() => handleLandCoverModeChange("2021")}
                >
                  NLCD 2021
                </button>
                <button
                  className={`flex-1 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                    landCoverMode === "change"
                      ? "bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] border-transparent"
                      : "border-[hsl(var(--border))] text-[hsl(var(--foreground))] bg-[hsl(var(--card))]"
                  }`}
                  onClick={() => handleLandCoverModeChange("change")}
                >
                  Change 2019–2021
                </button>
              </div>

              <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                  Filter by Category
                </p>
                <div className="flex flex-col gap-1.5">
                  {ALL_CATEGORIES.map((cat) => {
                    const codes = NLCD_CATEGORY_CLASSES[cat];
                    const sampleColor = NLCD_CLASSES[codes[0]]?.color ?? "#888";
                    return (
                      <label
                        key={cat}
                        className="flex items-center gap-2 cursor-pointer select-none text-xs"
                      >
                        <input
                          type="checkbox"
                          checked={enabledCategories.includes(cat)}
                          onChange={() => handleToggleCategory(cat)}
                          className="rounded"
                        />
                        <span
                          className="w-4 h-3 rounded-sm shrink-0"
                          style={{ backgroundColor: sampleColor }}
                        />
                        <span className="text-[hsl(var(--foreground))]">{cat}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            </TabsContent>

            {/* Reforestation Tab */}
            <TabsContent value="reforestation" className="flex flex-col gap-4 mt-4">
              {!bbox && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Pan/zoom the map to load reforestation opportunity zones for the current view.
                </p>
              )}

              {zonesQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Loading opportunity zones…
                </p>
              )}

              {!zonesQuery.isLoading && zonesUnavailable && (
                <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                  Opportunity zones are unavailable until a validated model output is published.
                </p>
              )}

              {!zonesQuery.isLoading && zonesFailed && (
                <p className="text-xs text-[hsl(var(--destructive))]">
                  Opportunity-zone data could not be loaded.
                </p>
              )}

              {!zonesQuery.isLoading &&
                !zonesUnavailable &&
                !zonesFailed &&
                zones.length > 0 && (
                  <>
                  <div className="grid grid-cols-3 gap-2">
                    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-center">
                      <span
                        className="block text-2xl font-bold"
                        style={{ color: "#1a7a1a" }}
                      >
                        {highCount}
                      </span>
                      <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
                        High
                      </span>
                    </div>
                    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-center">
                      <span
                        className="block text-2xl font-bold"
                        style={{ color: "#4caf50" }}
                      >
                        {mediumCount}
                      </span>
                      <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
                        Medium
                      </span>
                    </div>
                    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-center">
                      <span
                        className="block text-2xl font-bold"
                        style={{ color: "#9e9d24" }}
                      >
                        {lowCount}
                      </span>
                      <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
                        Low
                      </span>
                    </div>
                  </div>

                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                    <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                      Suitability Legend
                    </p>
                    <div className="flex flex-col gap-1">
                      <ColorLegendRow color="#1a7a1a" label="High — tree planting priority" />
                      <ColorLegendRow color="#4caf50" label="Medium — restoration candidate" />
                      <ColorLegendRow color="#cddc39" label="Low — marginal suitability" />
                    </div>
                  </div>

                  <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                    Zones derived from degraded NLCD classes (shrub, grassland, pasture) within
                    current map view. Click a zone on the map to see detailed suitability factors.
                  </p>
                  </>
                )}

              {!zonesQuery.isLoading &&
                !zonesUnavailable &&
                !zonesFailed &&
                zones.length === 0 &&
                bbox && (
                  <p className="text-xs text-[hsl(var(--muted-foreground))]">
                    No reforestation opportunity zones found in the current view.
                  </p>
                )}
            </TabsContent>
          </Tabs>
        </div>
      </SheetContent>
    </Sheet>
  );
}
