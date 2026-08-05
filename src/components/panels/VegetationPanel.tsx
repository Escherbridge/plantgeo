"use client";

import { useState } from "react";
import { Leaf, Layers, TreePine } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { trpc } from "@/lib/trpc/client";
import {
  ENVIRONMENTAL_TILES_CONFIGURED,
  NDVI_ANOMALY_UNAVAILABLE_REASON,
  NDVI_COLOR_RAMP,
  NDWI_COLOR_RAMP,
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
import type { VegetationMode } from "@/components/map/layers/VegetationLayer";
import type { LandCoverMode } from "@/components/map/layers/LandCoverLayer";

const ALL_CATEGORIES = Object.keys(NLCD_CATEGORY_CLASSES) as NLCDCategory[];

interface VegetationPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bbox?: string;
  // Callbacks to drive the map layers
  onVegetationModeChange?: (mode: VegetationMode) => void;
  onNDVIModeChange?: (mode: "absolute" | "anomaly") => void;
  onShowNDWIChange?: (show: boolean) => void;
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
  onVegetationModeChange,
  onNDVIModeChange,
  onShowNDWIChange,
  onLandCoverModeChange,
  onEnabledCategoriesChange,
}: VegetationPanelProps) {
  const vegStore = useVegetationStore();
  const anomalyMode = vegStore.ndviMode === "anomaly";
  const showNDWI = vegStore.showNDWI;
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

  function handleAnomalyToggle() {
    const next = !anomalyMode;
    vegStore.setNDVIMode(next ? "anomaly" : "absolute");
    onNDVIModeChange?.(next ? "anomaly" : "absolute");
  }

  function handleNDWIToggle() {
    const next = !showNDWI;
    vegStore.setShowNDWI(next);
    vegStore.setMode(next ? "ndwi" : "ndvi");
    onShowNDWIChange?.(next);
    if (next) onVegetationModeChange?.("ndwi");
    else onVegetationModeChange?.("ndvi");
  }

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

        <LayerToggle layerId="vegetation" label="Vegetation (NDVI)" />

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
            {/* Only claims a composite is drawn when one exists -- the gap notice below owns
                the other case, and saying "NDVI draws Jul 2024" for a period GIBS never
                published would be exactly the silent substitution this readout prevents. */}
            {compositePeriod !== null && compositeUnavailableReason === null && (
              <>
                {" — NDVI draws the "}
                <span className="font-medium text-[hsl(var(--foreground))]">
                  {compositePeriod}
                </span>
                {" composite; the period follows the map date, month by month."}
              </>
            )}
          </p>
        )}

        {/* Outside the tabs on purpose: the day is outside what GIBS publishes, so the raster
            is genuinely absent rather than switched off or still loading, and that must be
            legible the moment the panel opens rather than only on the NDVI tab. On the page,
            not in a title -- there is no focusable control here to hang one on. */}
        {compositeUnavailableReason !== null && (
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

              {!ENVIRONMENTAL_TILES_CONFIGURED && (
                <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                  Vegetation tiles are paused until a versioned first-party
                  warehouse release is published.
                </p>
              )}

              <div className="flex items-center justify-between">
                <label
                  htmlFor="ndvi-anomaly-toggle"
                  className="text-xs text-[hsl(var(--foreground))] cursor-not-allowed opacity-60"
                >
                  Anomaly mode
                  {/* The reason is on the page, not only in a title: a disabled control
                      isn't focusable. See TimeSlider's forecast-variant hint. */}
                  <span className="block text-[10px] text-amber-600 dark:text-amber-400">
                    {NDVI_ANOMALY_UNAVAILABLE_REASON}
                  </span>
                </label>
                <input
                  id="ndvi-anomaly-toggle"
                  type="checkbox"
                  checked={anomalyMode}
                  onChange={handleAnomalyToggle}
                  disabled
                  aria-disabled="true"
                  title={NDVI_ANOMALY_UNAVAILABLE_REASON}
                  className="rounded disabled:cursor-not-allowed disabled:opacity-50"
                />
              </div>

              <div className="flex items-center justify-between">
                <label
                  htmlFor="ndvi-ndwi-toggle"
                  className="text-xs text-[hsl(var(--foreground))] cursor-not-allowed opacity-60"
                >
                  Show NDWI (water stress)
                  {/* The reason is on the page, not only in a title: a disabled control
                      isn't focusable. See TimeSlider's forecast-variant hint. */}
                  <span className="block text-[10px] text-amber-600 dark:text-amber-400">
                    {NDWI_UNAVAILABLE_REASON}
                  </span>
                </label>
                <input
                  id="ndvi-ndwi-toggle"
                  type="checkbox"
                  checked={showNDWI}
                  onChange={handleNDWIToggle}
                  disabled
                  aria-disabled="true"
                  title={NDWI_UNAVAILABLE_REASON}
                  className="rounded disabled:cursor-not-allowed disabled:opacity-50"
                />
              </div>

              <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                  {showNDWI ? "NDWI Legend" : "NDVI Legend"}
                </p>
                <div className="flex flex-col gap-1">
                  {(showNDWI ? NDWI_COLOR_RAMP : NDVI_COLOR_RAMP).map((stop) => (
                    <ColorLegendRow key={stop.color} color={stop.color} label={stop.label} />
                  ))}
                </div>
              </div>
            </TabsContent>

            {/* Land Cover Tab */}
            <TabsContent value="landcover" className="flex flex-col gap-4 mt-4">
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
