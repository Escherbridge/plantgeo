"use client";

import { useMemo, useState } from "react";
import { Layers, Leaf, TrendingUp } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { trpc } from "@/lib/trpc/client";
import { GIBS_NDVI_PRODUCT, NDVI_COLOR_RAMP } from "@/lib/vegetation";
import { useVegetationStore } from "@/stores/vegetation-store";
import {
  useLayerDay,
  useLayerRenderState,
  useVegetationDisplayMode,
} from "@/lib/map/layer-toggle-context";
import { LayerOpacitySlider } from "@/components/ui/layer-opacity-slider";
import { ForecastBandChart } from "@/components/dashboard/ForecastBandChart";
import { hasFullBand } from "@/components/dashboard/chart-scales";
import { ndviForecastSeriesKeyForPoint } from "@/lib/forecast/series-key";
import { useMapStore } from "@/stores/map-store";
import { PROXIED_RETRY_COUNT } from "@/hooks/useViewportProxiedLayers";
import type { VegetationSource } from "@/components/map/layers/VegetationLayer";

// Published forecasts change on publication cadence (roughly daily), so ten minutes
// trades invisible staleness for not re-paying the proxy on every dock cycle.
const FORECAST_STALE_TIME_MS = 10 * 60 * 1000;

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
  // Controlled so the forecast proxy query can gate on its tab actually being on
  // screen; typed so a drifted trigger value breaks the build, not the gate.
  const [activeTab, setActiveTab] = useState<"ndvi" | "landcover" | "forecast">("ndvi");

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

  // The forecast plane is series-keyed, not viewport-keyed: the 0.25° cell under the
  // view centre stands in for "this view", matching the cells the measured grid draws.
  // The derived key only changes when the centre crosses a cell boundary, so panning
  // inside one cell never refetches.
  const viewportLatitude = useMapStore((state) => state.viewport.latitude);
  const viewportLongitude = useMapStore((state) => state.viewport.longitude);
  const forecastSeriesKey = useMemo(
    () => ndviForecastSeriesKeyForPoint(viewportLatitude, viewportLongitude),
    [viewportLatitude, viewportLongitude]
  );

  // Priced like every proxied upstream query (§proxied-viewport-queries): one retry,
  // a stale time that survives dock collapse/expand, and no fetch until the Forecast
  // tab is the one on screen.
  const forecastQuery = trpc.forecasts.getSeries.useQuery(
    { seriesKey: forecastSeriesKey },
    {
      enabled: activeTab === "forecast",
      retry: PROXIED_RETRY_COUNT,
      staleTime: FORECAST_STALE_TIME_MS,
    }
  );
  const forecast = forecastQuery.data;
  const forecastUnavailable = forecast?.availability === "unavailable";
  const forecastView = useMemo(() => {
    const points = forecast?.points ?? [];
    // All-or-nothing on both quantities: a line that silently switched from median
    // to point value mid-series would be captioned as something it is not.
    const lineIsMedian = points.length > 0 && points.every((point) => point.p50 !== null);
    const chartData = points.map((point) => ({
      time: point.validTime.slice(5, 10),
      median: lineIsMedian ? (point.p50 as number) : point.pointValue,
      low: point.p10,
      high: point.p90,
    }));
    return { lineIsMedian, hasBand: hasFullBand(chartData), chartData };
  }, [forecast]);

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
        <Tabs
          value={activeTab}
          onValueChange={(value) => setActiveTab(value as "ndvi" | "landcover" | "forecast")}
        >
          <TabsList className="w-full">
            <TabsTrigger value="ndvi" className="flex-1 text-xs">
              <Leaf className="h-3.5 w-3.5 mr-1" />
              NDVI
            </TabsTrigger>
            <TabsTrigger value="landcover" className="flex-1 text-xs">
              <Layers className="h-3.5 w-3.5 mr-1" />
              Land Cover
            </TabsTrigger>
            <TabsTrigger value="forecast" className="flex-1 text-xs">
              <TrendingUp className="h-3.5 w-3.5 mr-1" />
              Forecast
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

          </TabsContent>

          {/* Land Cover Tab */}
          <TabsContent value="landcover" className="flex flex-col gap-4 mt-4">
            {/* This notice belongs here, not on the NDVI tab: NDVI is served -- either from
                the warehouse cells or the GIBS proxy -- while land cover is exactly what
                ENVIRONMENTAL_TILES_CONFIGURED gates, so on the NDVI tab it contradicted a
                layer the reader could plainly see drawing. */}
            {/* A product selector and a category filter stood here, over a layer that has
                never drawn: `ENVIRONMENTAL_TILES_CONFIGURED` is a literal `false`, there is no
                NLCD producer, and geo.osm_landuse holds no rows. They are deleted rather than
                explained -- a control the reader can operate teaches that operating it does
                something, and no caption undoes that. The class vocabulary itself is kept in
                src/lib/environmental/nlcd.ts, ready for the day a producer publishes. */}
            <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
              No land-cover product is published for this platform yet. Nothing is drawn, and
              there are no controls here until there is something for them to control.
            </p>
          </TabsContent>

          {/* Forecast Tab */}
          <TabsContent value="forecast" className="flex flex-col gap-4 mt-4">
            {forecastQuery.isLoading && (
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                Loading published forecast…
              </p>
            )}

            {forecastQuery.isError && (
              <p className="text-xs text-[hsl(var(--destructive))]">
                The published forecast could not be loaded.
              </p>
            )}

            {forecastUnavailable && (
              <p
                className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
                data-testid="vegetation-forecast-unavailable"
              >
                The forecast service is not configured for this deployment, so no published
                forecast can be served.
              </p>
            )}

            {/* The expected state until a forecast run publishes receipts: the serving
                plane answered, and it holds nothing for this cell. Distinct from the
                unavailable notice above -- an empty answer is still an answer. */}
            {forecast && !forecastUnavailable && forecast.points.length === 0 && (
              <p
                className="text-xs text-[hsl(var(--muted-foreground))]"
                data-testid="vegetation-forecast-empty"
              >
                No forecast has been published for the 0.25° cell at the view centre yet.
                This panel fills in the moment a forecast run publishes receipts for it.
              </p>
            )}

            {forecast && forecast.points.length > 0 && (
              <>
                <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                  <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                    {forecast.metricName === "ndvi"
                      ? "NDVI forecast"
                      : `${forecast.metricName ?? "Metric"} forecast`}
                  </p>
                  <ForecastBandChart
                    data={forecastView.chartData}
                    width={320}
                    height={180}
                    color="#4caf50"
                    unit={forecast.metricUnit === "ndvi_index" ? undefined : forecast.metricUnit ?? undefined}
                  />
                  {/* The caption promises only what is drawn: the band claim drops when a
                      quantile is missing, and the line is named by what it plots. */}
                  <p className="mt-2 text-[10px] text-[hsl(var(--muted-foreground))] leading-relaxed">
                    {forecastView.hasBand
                      ? "The shaded band spans the published p10–p90; "
                      : "No uncertainty band was published; "}
                    the line is the{" "}
                    {forecastView.lineIsMedian ? "median" : "point forecast"}. Drawn for the
                    0.25° cell at the view centre ({forecast.entityKey}).
                  </p>
                </div>

                <p className="text-[10px] text-[hsl(var(--muted-foreground))] leading-relaxed">
                  {forecast.forecastMethod === "ml" ? "ML lane" : "Statistical baseline"}{" "}
                  forecast, issued {forecast.issuedAt?.slice(0, 10) ?? "—"}
                  {forecast.publishedAt !== null &&
                    `, published ${forecast.publishedAt.slice(0, 10)}`}
                  .{forecast.hasMore && " Showing the first page of a longer horizon."}
                  {forecast.staleReceiptPointsDropped > 0 &&
                    " A superseded forecast run on this page is not drawn."}
                </p>
              </>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
