"use client";

import { useState } from "react";
import { Leaf, Layers, TreePine } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Slider } from "@/components/ui/slider";
import { trpc } from "@/lib/trpc/client";
import { NDVI_COLOR_RAMP, NDWI_COLOR_RAMP } from "@/lib/vegetation";
import { NLCD_CATEGORY_CLASSES, NLCD_CLASSES, type NLCDCategory } from "@/lib/server/services/nlcd";
import { useVegetationStore } from "@/stores/vegetation-store";
import { LayerToggle } from "@/components/ui/layer-toggle";
import type { VegetationMode } from "@/components/map/layers/VegetationLayer";
import type { LandCoverMode } from "@/components/map/layers/LandCoverLayer";

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

const ALL_CATEGORIES = Object.keys(NLCD_CATEGORY_CLASSES) as NLCDCategory[];

interface VegetationPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bbox?: string;
  // Callbacks to drive the map layers
  onVegetationModeChange?: (mode: VegetationMode) => void;
  onYearChange?: (year: number) => void;
  onMonthChange?: (month: number) => void;
  onNDVIModeChange?: (mode: "absolute" | "anomaly") => void;
  onShowNDWIChange?: (show: boolean) => void;
  onLandCoverModeChange?: (mode: LandCoverMode) => void;
  onEnabledCategoriesChange?: (cats: NLCDCategory[]) => void;
}

// GIBS NDVI monthly composites have ~2 month processing delay
const MAX_YEAR = new Date().getFullYear() - 1;
const MIN_YEAR = 2000;

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
  onYearChange,
  onMonthChange,
  onNDVIModeChange,
  onShowNDWIChange,
  onLandCoverModeChange,
  onEnabledCategoriesChange,
}: VegetationPanelProps) {
  const vegStore = useVegetationStore();
  const year = vegStore.year;
  const month = vegStore.month;
  const anomalyMode = vegStore.ndviMode === "anomaly";
  const showNDWI = vegStore.showNDWI;
  const [landCoverMode, setLandCoverMode] = useState<LandCoverMode>("2021");
  const [enabledCategories, setEnabledCategories] = useState<NLCDCategory[]>([...ALL_CATEGORIES]);

  const zonesQuery = trpc.environmental.getReforestationZones.useQuery(
    { bbox: bbox ?? "" },
    { enabled: open && !!bbox }
  );

  const zones = zonesQuery.data?.features ?? [];
  const highCount = zones.filter(
    (f) => (f.properties as Record<string, unknown>).suitability === "High"
  ).length;
  const mediumCount = zones.filter(
    (f) => (f.properties as Record<string, unknown>).suitability === "Medium"
  ).length;
  const lowCount = zones.filter(
    (f) => (f.properties as Record<string, unknown>).suitability === "Low"
  ).length;

  function handleYearChange(val: number) {
    vegStore.setYear(val);
    onYearChange?.(val);
  }

  function handleMonthChange(val: number) {
    vegStore.setMonth(val);
    onMonthChange?.(val);
  }

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
              <div className="flex flex-col gap-2">
                <div className="flex justify-between text-xs text-[hsl(var(--muted-foreground))]">
                  <span>Year</span>
                  <span className="font-medium text-[hsl(var(--foreground))]">{year}</span>
                </div>
                <Slider
                  min={MIN_YEAR}
                  max={MAX_YEAR}
                  step={1}
                  value={year}
                  onValueChange={handleYearChange}
                />
              </div>

              <div className="flex flex-col gap-2">
                <div className="flex justify-between text-xs text-[hsl(var(--muted-foreground))]">
                  <span>Month</span>
                  <span className="font-medium text-[hsl(var(--foreground))]">
                    {MONTHS[month - 1]}
                  </span>
                </div>
                <Slider
                  min={1}
                  max={12}
                  step={1}
                  value={month}
                  onValueChange={handleMonthChange}
                />
                <div className="flex justify-between text-[10px] text-[hsl(var(--muted-foreground))] px-0.5">
                  {MONTHS.map((m) => (
                    <span key={m}>{m[0]}</span>
                  ))}
                </div>
              </div>

              <div className="flex items-center justify-between">
                <label className="text-xs text-[hsl(var(--foreground))] cursor-pointer">
                  Anomaly mode
                  <span className="block text-[10px] text-[hsl(var(--muted-foreground))]">
                    Deviation from 10-yr average
                  </span>
                </label>
                <input
                  type="checkbox"
                  checked={anomalyMode}
                  onChange={handleAnomalyToggle}
                  className="rounded"
                />
              </div>

              <div className="flex items-center justify-between">
                <label className="text-xs text-[hsl(var(--foreground))] cursor-pointer">
                  Show NDWI (water stress)
                  <span className="block text-[10px] text-[hsl(var(--muted-foreground))]">
                    Vegetation water content
                  </span>
                </label>
                <input
                  type="checkbox"
                  checked={showNDWI}
                  onChange={handleNDWIToggle}
                  className="rounded"
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

              {!zonesQuery.isLoading && zones.length > 0 && (
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

              {!zonesQuery.isLoading && zones.length === 0 && bbox && (
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
