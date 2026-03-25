"use client";

import { Droplets, CloudRain, Map } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { trpc } from "@/lib/trpc/client";
import { DROUGHT_LEGEND } from "@/components/map/layers/DroughtLayer";
import { LayerToggle } from "@/components/ui/layer-toggle";
import { DEMO_WATER_GAUGES, DEMO_DROUGHT_GEOJSON } from "@/lib/map/demo-data";

interface WaterPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bbox?: string;
}

const CONDITION_COLORS: Record<string, string> = {
  above_normal: "#2196f3",
  normal: "#009688",
  below_normal: "#ffeb3b",
  low: "#ff9800",
  critically_low: "#f44336",
  unknown: "#9e9e9e",
};

const CONDITION_LABEL: Record<string, string> = {
  above_normal: "Above Normal",
  normal: "Normal",
  below_normal: "Below Normal",
  low: "Low",
  critically_low: "Critically Low",
  unknown: "Unknown",
};

const TREND_SYMBOL: Record<string, string> = {
  rising: "↑",
  stable: "→",
  declining: "↓",
  critical: "↓↓",
};

function ColorSwatch({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-4 h-3 rounded-sm shrink-0" style={{ backgroundColor: color }} />
      <span className="text-[hsl(var(--muted-foreground))] text-xs">{label}</span>
    </div>
  );
}

export function WaterPanel({ open, onOpenChange, bbox }: WaterPanelProps) {
  const streamflowQuery = trpc.environmental.getStreamflow.useQuery(
    { bbox: bbox ?? "" },
    { enabled: open && !!bbox }
  );

  const droughtQuery = trpc.environmental.getDroughtClassification.useQuery(undefined, {
    enabled: open,
  });

  const watershedQuery = trpc.environmental.getWatersheds.useQuery(
    { bbox: bbox ?? "" },
    { enabled: open && !!bbox }
  );

  // Fall back to demo data when tRPC queries fail or return empty
  const gauges = streamflowQuery.data ?? (streamflowQuery.isError ? DEMO_WATER_GAUGES : []);
  const watersheds = watershedQuery.data?.features ?? [];

  // Summary counts per condition
  const conditionCounts = gauges.reduce<Record<string, number>>((acc, g) => {
    acc[g.condition] = (acc[g.condition] ?? 0) + 1;
    return acc;
  }, {});

  // Dominant drought class from the GeoJSON features (fall back to demo data on error)
  const droughtFeatures = droughtQuery.data?.features ?? (droughtQuery.isError ? DEMO_DROUGHT_GEOJSON.features : []);
  const dmCounts: Record<number, number> = {};
  for (const f of droughtFeatures) {
    const dm = (f.properties as Record<string, unknown>)?.DM as number | undefined;
    if (dm !== undefined && dm !== null) {
      dmCounts[dm] = (dmCounts[dm] ?? 0) + 1;
    }
  }
  const dominantDM =
    Object.keys(dmCounts).length > 0
      ? Number(Object.entries(dmCounts).sort((a, b) => b[1] - a[1])[0][0])
      : null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" onOpenChange={onOpenChange}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Droplets className="h-5 w-5 text-blue-500" />
            Water Scarcity
          </SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-1.5 mt-2">
          <LayerToggle layerId="water" label="Water Gauges" />
          <LayerToggle layerId="drought" label="Drought Monitor" />
        </div>

        <div className="mt-4 overflow-y-auto max-h-[calc(100vh-8rem)]">
          <Tabs defaultValue="streamflow">
            <TabsList className="w-full">
              <TabsTrigger value="streamflow" className="flex-1 text-xs">
                <Droplets className="h-3.5 w-3.5 mr-1" />
                Streamflow
              </TabsTrigger>
              <TabsTrigger value="drought" className="flex-1 text-xs">
                <CloudRain className="h-3.5 w-3.5 mr-1" />
                Drought
              </TabsTrigger>
              <TabsTrigger value="watersheds" className="flex-1 text-xs">
                <Map className="h-3.5 w-3.5 mr-1" />
                Watersheds
              </TabsTrigger>
            </TabsList>

            {/* Streamflow tab */}
            <TabsContent value="streamflow" className="flex flex-col gap-4 mt-4">
              {!bbox && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Pan/zoom the map to load USGS streamflow gauges for the current view.
                </p>
              )}

              {streamflowQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Loading streamflow data…
                </p>
              )}

              {streamflowQuery.isError && (
                <p className="text-xs text-amber-500">
                  Live USGS data unavailable. Showing demo gauge data.
                </p>
              )}

              {!streamflowQuery.isLoading && gauges.length > 0 && (
                <>
                  <div className="grid grid-cols-3 gap-2 text-center">
                    {Object.entries(conditionCounts).map(([condition, count]) => (
                      <div
                        key={condition}
                        className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-2"
                      >
                        <span
                          className="block text-xl font-bold"
                          style={{ color: CONDITION_COLORS[condition] ?? "#9e9e9e" }}
                        >
                          {count}
                        </span>
                        <span className="text-[9px] text-[hsl(var(--muted-foreground))] leading-tight">
                          {CONDITION_LABEL[condition] ?? condition}
                        </span>
                      </div>
                    ))}
                  </div>

                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                    <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                      Gauges ({gauges.length})
                    </p>
                    <div className="flex flex-col gap-1.5 max-h-64 overflow-y-auto">
                      {gauges.map((g) => (
                        <div
                          key={g.siteNo}
                          className="flex items-center justify-between gap-2 text-xs"
                        >
                          <span className="text-[hsl(var(--foreground))] truncate flex-1">
                            {g.siteName || `USGS ${g.siteNo}`}
                          </span>
                          <div className="flex items-center gap-1 shrink-0">
                            <span
                              className="px-1.5 py-0.5 rounded text-[9px] font-medium text-white"
                              style={{ backgroundColor: CONDITION_COLORS[g.condition] ?? "#9e9e9e" }}
                            >
                              {CONDITION_LABEL[g.condition] ?? g.condition}
                            </span>
                            {g.trend && (
                              <span className="text-[hsl(var(--muted-foreground))]">
                                {TREND_SYMBOL[g.trend]}
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                    <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                      Condition Legend
                    </p>
                    <div className="flex flex-col gap-1">
                      {Object.entries(CONDITION_COLORS)
                        .filter(([k]) => k !== "unknown")
                        .map(([condition, color]) => (
                          <ColorSwatch
                            key={condition}
                            color={color}
                            label={CONDITION_LABEL[condition] ?? condition}
                          />
                        ))}
                    </div>
                  </div>
                </>
              )}

              {!streamflowQuery.isLoading && gauges.length === 0 && bbox && !streamflowQuery.isError && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  No active USGS streamflow gauges found in the current view.
                </p>
              )}
            </TabsContent>

            {/* Drought tab */}
            <TabsContent value="drought" className="flex flex-col gap-4 mt-4">
              {droughtQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Loading US Drought Monitor data…
                </p>
              )}

              {droughtQuery.isError && (
                <p className="text-xs text-amber-500">
                  Live USDM data unavailable. Showing demo drought data.
                </p>
              )}

              {!droughtQuery.isLoading && droughtFeatures.length > 0 && (
                <>
                  {dominantDM !== null && (
                    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-center">
                      <p className="text-xs text-[hsl(var(--muted-foreground))] mb-1">
                        Dominant Classification
                      </p>
                      <span
                        className="inline-block px-3 py-1 rounded font-bold text-sm text-white"
                        style={{
                          backgroundColor:
                            dominantDM === 0
                              ? DROUGHT_LEGEND[1].color
                              : dominantDM === 1
                                ? DROUGHT_LEGEND[2].color
                                : dominantDM === 2
                                  ? DROUGHT_LEGEND[3].color
                                  : dominantDM === 3
                                    ? DROUGHT_LEGEND[4].color
                                    : DROUGHT_LEGEND[5].color,
                          color: dominantDM <= 1 ? "#333" : "#fff",
                        }}
                      >
                        D{dominantDM} —{" "}
                        {dominantDM === 0
                          ? "Abnormally Dry"
                          : dominantDM === 1
                            ? "Moderate Drought"
                            : dominantDM === 2
                              ? "Severe Drought"
                              : dominantDM === 3
                                ? "Extreme Drought"
                                : "Exceptional Drought"}
                      </span>
                    </div>
                  )}

                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                    <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                      Drought Classification Legend
                    </p>
                    <div className="flex flex-col gap-1">
                      {DROUGHT_LEGEND.map((entry) => (
                        <ColorSwatch key={entry.label} color={entry.color} label={entry.label} />
                      ))}
                    </div>
                  </div>

                  <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                    Source: US Drought Monitor (USDA/NDMC). Updated weekly on Tuesdays.
                  </p>
                </>
              )}

              {!droughtQuery.isLoading && droughtFeatures.length === 0 && !droughtQuery.isError && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  No drought data available at this time.
                </p>
              )}
            </TabsContent>

            {/* Watersheds tab */}
            <TabsContent value="watersheds" className="flex flex-col gap-4 mt-4">
              {!bbox && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Pan/zoom the map to load watershed boundaries for the current view.
                </p>
              )}

              {watershedQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Loading watershed data…
                </p>
              )}

              {watershedQuery.isError && (
                <p className="text-xs text-red-500">
                  Failed to load watershed data. USGS NHD may be unavailable.
                </p>
              )}

              {!watershedQuery.isLoading && watersheds.length > 0 && (
                <>
                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-center">
                    <span className="block text-2xl font-bold text-blue-600">
                      {watersheds.length}
                    </span>
                    <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
                      Watersheds in View
                    </span>
                  </div>

                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                    <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                      Watershed Boundaries
                    </p>
                    <div className="flex flex-col gap-1.5 max-h-56 overflow-y-auto">
                      {watersheds.slice(0, 50).map((f, i) => {
                        const props = f.properties as Record<string, unknown> | null;
                        const name =
                          (props?.["Name"] as string) ??
                          (props?.["HUC12"] as string) ??
                          `Watershed ${i + 1}`;
                        return (
                          <div key={i} className="flex items-center gap-2 text-xs">
                            <span className="w-3 h-3 rounded-sm bg-blue-600 opacity-60 shrink-0" />
                            <span className="text-[hsl(var(--foreground))] truncate">{name}</span>
                          </div>
                        );
                      })}
                      {watersheds.length > 50 && (
                        <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                          +{watersheds.length - 50} more…
                        </p>
                      )}
                    </div>
                  </div>

                  <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                    Source: USGS National Hydrography Dataset Plus HR (HUC12). Click a watershed
                    outline on the map for details.
                  </p>
                </>
              )}

              {!watershedQuery.isLoading && watersheds.length === 0 && bbox && !watershedQuery.isError && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  No watershed boundaries found in the current view.
                </p>
              )}
            </TabsContent>
          </Tabs>
        </div>
      </SheetContent>
    </Sheet>
  );
}
