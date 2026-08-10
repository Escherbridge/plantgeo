"use client";

import { Droplets, CloudRain, Map } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { trpc } from "@/lib/trpc/client";
import {
  useWatershedsQuery,
  WATERSHED_LIST_MAX_SQUARE_DEGREES,
} from "@/hooks/useViewportProxiedLayers";
import { bboxSquareDegrees } from "@/lib/map/viewport-bbox";
import { DROUGHT_LEGEND } from "@/components/map/layers/DroughtLayer";
import {
  useDebouncedLayerDay,
  useLayerDay,
  useLayerRenderState,
} from "@/lib/map/layer-toggle-context";

interface WaterDetailsProps {
  /** The map's viewport, from the one `useViewportBounds()` derivation LayerManager reads. */
  bbox?: string;
}

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

/**
 * Discharge, at the precision the reading carries. Small flows are the ones a single decimal
 * matters to; a river running 40,000 cfs is not measured to a tenth.
 */
function formatCfs(value: number): string {
  return value >= 100
    ? Math.round(value).toLocaleString()
    : value.toFixed(1);
}

function SummaryStat({ value, label }: { value: number | string; label: string }) {
  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-2">
      <span className="block text-xl font-bold text-[hsl(var(--foreground))]">{value}</span>
      <span className="text-[9px] text-[hsl(var(--muted-foreground))] leading-tight">
        {label}
      </span>
    </div>
  );
}

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

/**
 * What the water layers are showing, as the Water section of the map dock.
 *
 * Every `enabled` flag below used to carry an `open &&` term, from the sheet this was. It is
 * gone rather than hardcoded to `true` by accident: this component is MOUNTED only while its
 * dock section is expanded, so being mounted is what "open" now means, and a collapsed section
 * costs no query at all. The five `<LayerToggle>` rows went with the sheet -- the section's own
 * layer rows are the switches.
 */
export function WaterDetails({ bbox }: WaterDetailsProps) {
  // TWO days, because this section describes two layers and each carries its own slider on its
  // own row since 2026-08-09. They are the same settled days LayerManager sends for the same
  // toggles, so each tab and the map it describes share one cache entry rather than asking the
  // warehouse for two different days -- but the gauge day and the drought day are now genuinely
  // independent, and nothing here may present one as covering both.
  const waterDay = useDebouncedLayerDay("water");
  const droughtDay = useDebouncedLayerDay("drought");

  const streamflowQuery = trpc.environmental.getStreamflow.useQuery(
    { bbox: bbox ?? "", date: waterDay.requestDate },
    { enabled: !!bbox }
  );

  // tRPC keys `undefined` input differently from an object, so the dateless case must stay
  // literally undefined rather than becoming `{ date: undefined }`.
  const droughtQuery = trpc.environmental.getDroughtClassification.useQuery(
    droughtDay.requestDate === undefined ? undefined : { date: droughtDay.requestDate }
  );

  // The same hook LayerManager calls, so the boundaries this tab lists are the ones the
  // map already fetched: one query entry, not a second ~5 MB USGS request 60 s later.
  // See src/lib/server/AGENTS.md §proxied-viewport-queries.
  const watershedQuery = useWatershedsQuery(bbox, { enabled: true });

  // Each layer's own day, read raw rather than settled, because these are LABELS: a caption
  // must track the pointer even while the queries wait for it to stop.
  const gaugeSelectedDate = useLayerDay("water").selectedDate;
  const droughtSelectedDate = useLayerDay("drought").selectedDate;
  // The two feeds are on the same day only when they are, which is a fact to be checked and
  // never assumed -- opening on their own `latestObservedDate` puts them on different days by
  // default, since the gauges and USDM publish on different cadences. The caption below states
  // one date when they agree and both when they do not; asserting a shared "map date" over a
  // mixed-time composite is the mislabelling this whole feature exists to prevent.
  const feedDatesAgree = gaugeSelectedDate === droughtSelectedDate;
  const hasAnySelectedDay = gaugeSelectedDate !== null || droughtSelectedDate !== null;
  // The release actually served, which for a weekly product is on or before the drought day.
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
  // Wider than the proxy will answer for, so the list is empty by design rather than by fault.
  const watershedListArea = bbox === undefined ? null : bboxSquareDegrees(bbox);
  const beyondWatershedListZoom =
    watershedListArea !== null && watershedListArea > WATERSHED_LIST_MAX_SQUARE_DEGREES;
  const listedWatersheds = watersheds.slice(0, WATERSHED_LIST_LIMIT);

  // Gauges that actually reported a discharge value, and the middle of those readings. A
  // gauge with no value is not a gauge reading zero, so it is excluded from the median rather
  // than counted as a low flow.
  const reportingGauges = gauges.filter(
    (g): g is typeof g & { flowCfs: number } => g.flowCfs !== null
  );
  const medianFlowCfs =
    reportingGauges.length === 0
      ? null
      : [...reportingGauges].sort((a, b) => a.flowCfs - b.flowCfs)[
          Math.floor((reportingGauges.length - 1) / 2)
        ].flowCfs;

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
    <div className="flex flex-col">
      {/* One line per feed that has a day, never one date over all of them. Watersheds are
          named unconditionally rather than only on an off-today day, which is what the old
          caption did: the boundaries are proxied live from USGS per viewport and carry no date
          at ALL, so their being undated was never a function of which day was selected -- and
          beside two dated feeds on two different days, an unlabelled third would be read as
          sharing whichever one it sat next to. */}
      {hasAnySelectedDay && (
        <p
          className="rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-2 py-1.5 text-[11px] text-[hsl(var(--muted-foreground))]"
          data-testid="water-feed-dates"
        >
          {feedDatesAgree ? (
            <>
              Streamflow and drought read for{" "}
              <span className="font-medium text-[hsl(var(--foreground))]">
                {gaugeSelectedDate}
              </span>
            </>
          ) : (
            <>
              Streamflow{" "}
              <span className="font-medium text-[hsl(var(--foreground))]">
                {gaugeSelectedDate ?? "no date yet"}
              </span>
              {" · drought "}
              <span className="font-medium text-[hsl(var(--foreground))]">
                {droughtSelectedDate ?? "no date yet"}
              </span>
            </>
          )}
          {" — each layer scrubs its own row; watershed boundaries are not dated."}
        </p>
      )}

      {/* No `overflow-y-auto max-h-[calc(100vh-8rem)]` wrapper: the dock's body is the one
          scroller, and a second one nested inside it is the defect panel-scroll.ts rule 2
          exists to prevent. */}
      <div className="mt-3">
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
                {/* Counts of what was measured, in place of a condition tally whose every
                    bucket but "Unknown" was unreachable. Median is a summary OF the readings
                    in view, never a reading in its own right. */}
                <div className="grid grid-cols-3 gap-2 text-center">
                  <SummaryStat value={gauges.length} label="Gauges in view" />
                  <SummaryStat value={reportingGauges.length} label="Reporting discharge" />
                  <SummaryStat
                    value={medianFlowCfs === null ? "—" : formatCfs(medianFlowCfs)}
                    label="Median cfs"
                  />
                </div>

                <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                  <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                    Gauges ({gauges.length})
                  </p>
                  {/* Unbounded, and scrolled by the dock rather than by itself: the list is
                      already capped upstream, and a scroll box inside a 19rem dock is the
                      nested-scroller trap panel-scroll.ts rule 2 names. */}
                  <div className="flex flex-col gap-1.5">
                    {gauges.map((g) => (
                      <div
                        key={g.siteNo}
                        className="flex items-center justify-between gap-2 text-xs"
                      >
                        <span className="text-[hsl(var(--foreground))] truncate flex-1">
                          {g.siteName || `USGS ${g.siteNo}`}
                        </span>
                        {/* The discharge NWIS reported, which is the one number this feed
                            actually carries. The chip that stood here read "Unknown" on every
                            gauge in every view, forever -- see GAUGE_READING_COLORS. */}
                        <span
                          className={`shrink-0 tabular-nums ${
                            g.flowCfs === null
                              ? "text-[hsl(var(--muted-foreground))]"
                              : "text-[hsl(var(--foreground))] font-medium"
                          }`}
                        >
                          {g.flowCfs === null ? "not reported" : `${formatCfs(g.flowCfs)} cfs`}
                        </span>
                      </div>
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
                      {/* USDM is weekly, so the release covering the drought row's day is
                          normally dated before it. Naming the release is what keeps a
                          carried-forward map from reading as a same-day measurement. Compared
                          against the DROUGHT row's day, never the gauges' -- the two are
                          independent now, and against the wrong one this line would either
                          appear on a same-day release or vanish on a carried-forward one. */}
                      {droughtReleaseDate !== null &&
                        droughtReleaseDate !== droughtSelectedDate && (
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
                  {/* Capped at WATERSHED_LIST_LIMIT rows above, so the dock's own scroller is
                      enough; see the gauge list for why a nested one is not wanted. */}
                  <div className="flex flex-col gap-1.5">
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

            {/* The list is capped by the proxy, the map layer is not. Said here because the
                two disagree on purpose at wide zooms: basins are drawn (rolled up to the HUC
                level the zoom can carry, per 0023_watershed_zoom_generalization) while this
                list has nothing to show, and an unexplained empty list beside a drawn layer
                reads as a fault. */}
            {beyondWatershedListZoom && (
              <p className="rounded-md border border-sky-500/40 bg-sky-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                This view is wider than the {WATERSHED_LIST_MAX_SQUARE_DEGREES} square degree
                the USGS hydrography service will list basins for. The map still draws them,
                grouped into their parent basins — zoom in for the named HUC12 list.
              </p>
            )}

            {/* Only claimed once a response actually arrived: an undefined query result
                is "not answered yet", not "the provider says there are none here". */}
            {watershedQuery.data &&
              !watershedQuery.isLoading &&
              !watershedsUnavailable &&
              !beyondWatershedListZoom &&
              watersheds.length === 0 && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  USGS reports no HUC12 watersheds intersecting this view.
                </p>
              )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
