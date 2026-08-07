"use client";

import { Droplets, CloudRain, Map } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { trpc } from "@/lib/trpc/client";
import { useWatershedsQuery } from "@/hooks/useViewportProxiedLayers";
import { DROUGHT_LEGEND } from "@/components/map/layers/DroughtLayer";
import { CONDITION_COLORS } from "@/components/map/layers/WaterLayer";
import { LayerToggle } from "@/components/ui/layer-toggle";
import {
  useDebouncedMapDay,
  useLayerRenderState,
  useMapDay,
} from "@/lib/map/layer-toggle-context";

interface WaterPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bbox?: string;
}

const CONDITION_LABEL: Record<string, string> = {
  above_normal: "Above Normal",
  normal: "Normal",
  below_normal: "Below Normal",
  low: "Low",
  critically_low: "Critically Low",
  unknown: "Unknown",
};

// The gauge layer's geo.layers name lives in the layer registry ("water" -> "water-gauges").
// Drought has no geo.layers row, so the registry gives it none and it makes no claim.

/** Rows rendered in the watershed list; the header states the full count beside it. */
const WATERSHED_LIST_LIMIT = 50;

/**
 * Why the drought layer is empty, in the reader's own vocabulary. Keyed by
 * `PublishedDroughtCollection.reason` in
 * `src/lib/server/services/environmental-read-model.ts`, which is the source of truth for
 * these codes; an unrecognised one falls back rather than rendering a blank warning.
 *
 * A skipped release week is NOT a fault and must not read as one: the ingest lane recorded
 * that USDM published nothing that week, and the map is required to render it empty rather
 * than carry the previous week forward over it.
 */
const DROUGHT_UNAVAILABLE_COPY: Record<string, string> = {
  not_published: "No US Drought Monitor release covers the selected date.",
  release_week_not_published:
    "The US Drought Monitor published no release for this week, so nothing is drawn — the preceding week is deliberately not carried over it.",
  stale:
    "The newest stored US Drought Monitor release is too old to describe the selected date.",
  not_forecastable: "Drought classification is not forecast beyond today.",
  invalid_observation_time:
    "The stored US Drought Monitor release carries no readable date, so it is not drawn.",
};

/** Fallback when the reader reports a code this panel has no sentence for. */
const DROUGHT_UNAVAILABLE_FALLBACK =
  "No US Drought Monitor classification is available for the selected date.";

const TREND_SYMBOL: Record<string, string> = {
  rising: "↑",
  stable: "→",
  declining: "↓",
  critical: "↓↓",
};

/**
 * Trims a watershed attribute down to a usable list label, rejecting blanks.
 * WBDHU12 leaves fields like `states` null on some features, and `??` alone would
 * happily render an empty string as the row's name.
 */
function watershedLabel(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return value.trim() || null;
}

function ColorSwatch({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-4 h-3 rounded-sm shrink-0" style={{ backgroundColor: color }} />
      <span className="text-[hsl(var(--muted-foreground))] text-xs">{label}</span>
    </div>
  );
}

export function WaterPanel({ open, onOpenChange, bbox }: WaterPanelProps) {
  // The same settled day LayerManager sends, so the panel and the map share one cache entry
  // per feed rather than asking the warehouse for two different days.
  const { requestDate } = useDebouncedMapDay();

  const streamflowQuery = trpc.environmental.getStreamflow.useQuery(
    { bbox: bbox ?? "", date: requestDate },
    { enabled: open && !!bbox }
  );

  // tRPC keys `undefined` input differently from an object, so the dateless case must stay
  // literally undefined rather than becoming `{ date: undefined }`.
  const droughtQuery = trpc.environmental.getDroughtClassification.useQuery(
    requestDate === undefined ? undefined : { date: requestDate },
    { enabled: open }
  );

  // The same hook LayerManager calls, so the boundaries this tab lists are the ones the
  // map already fetched: one query entry, not a second ~5 MB USGS request 60 s later.
  // See src/lib/server/AGENTS.md §proxied-viewport-queries.
  const watershedQuery = useWatershedsQuery(bbox, { enabled: open });

  // The map's day, read from the toggle context, so the panel and the map can never
  // disagree about what is displayed. Read raw rather than settled, because this one is a
  // label: the caption must track the pointer even while the queries wait for it to stop.
  const mapDay = useMapDay();
  const selectedDate = mapDay.selectedDate;
  const hasSelectedDay = selectedDate !== null;
  // Streamflow and drought are read for the selected day; the watershed boundaries below are
  // proxied live from USGS per viewport and carry no date at all, so the caption says so
  // rather than letting one undated feed be read as if it shared the other two's day.
  const watershedsAreUndated = mapDay.isOffServerToday;
  // The release actually served, which for a weekly product is on or before the selected day.
  const droughtReleaseDate =
    droughtQuery.data?.observedAt?.slice(0, 10) ?? null;

  // The gauge layer's own availability at that day; the context names it "water-gauges".
  const gaugeReason = useLayerRenderState("water").unavailableReason;

  const gauges = streamflowQuery.data ?? [];
  const watersheds = watershedQuery.data?.features ?? [];
  const droughtUnavailable = droughtQuery.data?.availability === "unavailable";
  const watershedsUnavailable =
    watershedQuery.data?.availability === "unavailable";
  // USGS stops at its transfer limit and says so. The count below then describes a
  // subset of the view, so it must not be presented as the number of watersheds here.
  const watershedsTruncated = watershedQuery.data?.truncated === true;
  const listedWatersheds = watersheds.slice(0, WATERSHED_LIST_LIMIT);

  // Summary counts per condition
  const conditionCounts = gauges.reduce<Record<string, number>>((acc, g) => {
    acc[g.condition] = (acc[g.condition] ?? 0) + 1;
    return acc;
  }, {});

  const droughtFeatures = droughtQuery.data?.features ?? [];
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
            <Droplets aria-hidden="true" className="h-5 w-5 text-blue-500" />
            Water Scarcity
          </SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-1.5 mt-2">
          <LayerToggle layerId="water" label="Water Gauges" />
          <LayerToggle layerId="drought" label="Drought Monitor" />
          <LayerToggle layerId="weather" label="Wind & Weather" />
          {/* The registry has assigned "sensors" to this panel since the style layer
              landed, but no switch was ever added here -- so geo.sensor_tiles()' published
              stations served correctly and could be turned on by nothing. */}
          <LayerToggle layerId="sensors" label="Sensor Stations" />
          {/* The registry assigns "watersheds" to this panel, and the Legend only lists
              warehouse-backed layers -- so without a switch here the boundaries the tab
              below enumerates could never be drawn on the map. */}
          <LayerToggle layerId="watersheds" label="Watershed Boundaries" />
        </div>

        {hasSelectedDay && (
          <p className="mt-3 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-2 py-1.5 text-[11px] text-[hsl(var(--muted-foreground))]">
            Map date{" "}
            <span className="font-medium text-[hsl(var(--foreground))]">{selectedDate}</span>
            {watershedsAreUndated &&
              " — streamflow and drought below are read for this date; watershed boundaries are not dated."}
          </p>
        )}

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
              {/* Why the gauge layer has nothing for the selected day. Stated even while
                  the latest readings are listed below, so an empty map layer is never
                  mistaken for the toggle being off. */}
              {gaugeReason !== null && (
                <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                  {gaugeReason}
                </p>
              )}

              {!bbox && (
                <p
                  role="status"
                  aria-live="polite"
                  className="text-xs text-[hsl(var(--muted-foreground))]"
                >
                  Pan/zoom the map to load USGS streamflow gauges for the current view.
                </p>
              )}

              {streamflowQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Loading streamflow data…
                </p>
              )}

              {streamflowQuery.isError && (
                <p className="text-xs text-red-500" role="alert">
                  Verified USGS gauge data is temporarily unavailable. No synthetic readings are shown.
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
                  No recent verified USGS streamflow observations are available
                  in the current view.
                </p>
              )}
            </TabsContent>

            {/* Drought tab */}
            <TabsContent value="drought" className="flex flex-col gap-4 mt-4">
              {droughtQuery.isLoading && (
                <p
                  role="status"
                  aria-live="polite"
                  className="text-xs text-[hsl(var(--muted-foreground))]"
                >
                  Loading US Drought Monitor data…
                </p>
              )}

              {droughtQuery.isError && (
                <p className="text-xs text-red-500" role="alert">
                  Verified drought data is temporarily unavailable. No synthetic classifications are shown.
                </p>
              )}

              {!droughtQuery.isLoading && droughtFeatures.length > 0 && (
                <>
                  {dominantDM !== null && (
                    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-center">
                      <p className="text-xs text-[hsl(var(--muted-foreground))] mb-1">
                        Dominant Classification
                        {/* USDM is weekly, so the release covering the selected day is
                            normally dated before it. Naming the release is what keeps a
                            carried-forward map from reading as a same-day measurement. */}
                        {droughtReleaseDate !== null &&
                          droughtReleaseDate !== selectedDate && (
                            <span className="block text-[10px]">
                              As of the {droughtReleaseDate} release
                            </span>
                          )}
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

              {!droughtQuery.isLoading && droughtUnavailable && !droughtQuery.isError && (
                <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                  {DROUGHT_UNAVAILABLE_COPY[droughtQuery.data?.reason ?? ""] ??
                    DROUGHT_UNAVAILABLE_FALLBACK}
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
                <p className="text-xs text-red-500" role="alert">
                  Published watershed data could not be loaded. No provider or
                  synthetic fallback is shown.
                </p>
              )}

              {!watershedQuery.isLoading && watersheds.length > 0 && (
                <>
                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-center">
                    <span className="block text-2xl font-bold text-blue-600">
                      {watershedsTruncated ? `${watersheds.length}+` : watersheds.length}
                    </span>
                    <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
                      {watershedsTruncated
                        ? "Watersheds Returned (partial view)"
                        : "Watersheds in View"}
                    </span>
                  </div>

                  {watershedsTruncated && (
                    <p
                      role="status"
                      aria-live="polite"
                      className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
                    >
                      USGS reached its transfer limit for this view and returned only
                      the first {watersheds.length} boundaries. Zoom in for complete
                      coverage — the count above and the map outlines are a subset.
                    </p>
                  )}

                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                    <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                      Watershed Boundaries
                      {watersheds.length > WATERSHED_LIST_LIMIT &&
                        ` (showing ${listedWatersheds.length} of ${watersheds.length})`}
                    </p>
                    <div className="flex flex-col gap-1.5 max-h-56 overflow-y-auto">
                      {listedWatersheds.map((f, i) => {
                        const props = f.properties as Record<string, unknown> | null;
                        // The WBDHU12 layer's `f=geojson` output carries the layer's own
                        // lowercase field names, not the title-case aliases the ArcGIS
                        // catalog displays -- reading "Name"/"HUC12" matched nothing and
                        // listed every row as "Watershed N". Same spelling as
                        // formatWatershed in src/lib/map/hover-fields.ts.
                        const name =
                          watershedLabel(props?.["name"]) ??
                          watershedLabel(props?.["huc12"]) ??
                          `Watershed ${i + 1}`;
                        return (
                          <div key={i} className="flex items-center gap-2 text-xs">
                            <span className="w-3 h-3 rounded-sm bg-blue-600 opacity-60 shrink-0" />
                            <span className="text-[hsl(var(--foreground))] truncate">{name}</span>
                          </div>
                        );
                      })}
                      {watersheds.length > WATERSHED_LIST_LIMIT && (
                        <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                          +{watersheds.length - WATERSHED_LIST_LIMIT} more returned,
                          not listed here
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

              {/* Boundaries are proxied live from USGS per viewport, so there is no
                  warehouse release to wait on: "unavailable" here means the hydrography
                  service itself answered without a feature collection (ArcGIS reports
                  some faults as HTTP 200 plus an error object), which is a transient
                  upstream condition rather than a withheld capability. hydrosheds.ts
                  validates before it writes to Redis, so a fault is never cached and
                  reloading this same view really does re-ask the provider. */}
              {!watershedQuery.isLoading && watershedsUnavailable && bbox && !watershedQuery.isError && (
                <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                  The USGS hydrography service did not return boundaries for this view.
                  Nothing is drawn; the response was not cached, so reloading this view
                  asks the provider again.
                </p>
              )}

              {/* Only claimed once a response actually arrived: an undefined query result
                  is "not answered yet", not "the provider says there are none here". */}
              {watershedQuery.data &&
                !watershedQuery.isLoading &&
                !watershedsUnavailable &&
                watersheds.length === 0 && (
                  <p className="text-xs text-[hsl(var(--muted-foreground))]">
                    USGS reports no HUC12 watersheds intersecting this view.
                  </p>
                )}
            </TabsContent>
          </Tabs>
        </div>
      </SheetContent>
    </Sheet>
  );
}
