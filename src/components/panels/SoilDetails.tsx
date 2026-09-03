"use client";

import { Layers, Wind, Leaf } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { trpc } from "@/lib/trpc/client";
import { Fragment } from "react";
import {
  type SoilProperty,
  SOIL_PROPERTY_LABELS,
  SOIL_PROPERTY_POINT_FIELD,
} from "@/components/map/layers/SoilLayer";
import { useSoilStore } from "@/stores/soil-store";
import { EROSION_COLORS, type ErosionClass } from "@/lib/environmental/erosion";
import { CARBON_COLORS, classifyCarbonPotential, type CarbonClass } from "@/lib/environmental/carbon";
import type { InterventionType } from "@/lib/environmental/intervention";
import { useDebouncedLayerDay, useLayerVisibility } from "@/lib/map/layer-toggle-context";
import {
  useSoilFieldQuery,
  useSoilSurveyQuery,
} from "@/hooks/useViewportProxiedLayers";
import {
  SOIL_FIELD_MEASURE_IDS,
  soilFieldDepthDefinition,
  soilFieldMeasureDefinition,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
import { LANE_BASE_LATTICES } from "@/lib/map/zoom-tiers";

/**
 * The ground ERA5-Land actually measures, from the one lane table. The caption compares the
 * SERVED cell against it to say whether a rung really aggregated anything: below z0 the ladder's
 * grid is finer than this, so those rungs re-floor the same measurement rather than averaging
 * several, and claiming an average there would be a claim about data that never merged.
 */
const SOIL_FIELD_MEASURED_CELL_DEGREES = LANE_BASE_LATTICES["soil-field"].cellSizeDegrees;

interface SoilDetailsProps {
  /** Point to query for soil + intervention suitability */
  queryPoint?: { lat: number; lon: number } | null;
  /**
   * Drops the query pin. Supplied by the dock's Soil section, which owns the click capture --
   * collapsing the section clears the pin too, so this is the in-section escape hatch rather
   * than the only one. See `src/components/map/AGENTS.md` "Picking a point to query".
   */
  onClearQueryPoint?: () => void;
  /** The map's viewport, handed down by the dock exactly as the other sections get it. */
  bbox?: string;
  /**
   * The map's viewport zoom, from the same `useViewportBounds()` derivation the dock hands
   * `bbox` from. Selects the SSURGO survey's render granularity -- see `useSoilSurveyQuery`
   * and `src/lib/server/services/usda-soil.ts` §soil-survey-zoom. Omitted keeps the
   * pre-zoom-aware detail-tier behavior.
   */
  zoom?: number;
}

const SOIL_PROPERTY_OPTIONS: { value: SoilProperty; label: string }[] = [
  { value: "phh2o", label: "pH" },
  { value: "soc", label: "Organic Carbon" },
  { value: "nitrogen", label: "Nitrogen" },
  { value: "bdod", label: "Bulk Density" },
  { value: "cec", label: "CEC" },
  { value: "ocd", label: "Organic Carbon Density" },
];

/**
 * The queried-point card's rows, one per `SoilProperties` field. Selecting a property
 * above highlights its matching row here via `SOIL_PROPERTY_POINT_FIELD` -- previously
 * this card showed the same six values regardless of which property was selected, so
 * the selector had no visible effect on a drill-down query.
 */
const SOIL_POINT_FIELD_ROWS: {
  field: "ph" | "organicCarbon" | "nitrogen" | "bulkDensity" | "cec" | "ocd";
  label: string;
  format: (value: number) => string;
}[] = [
  { field: "ph", label: "pH", format: (v) => v.toFixed(1) },
  { field: "organicCarbon", label: "Organic Carbon", format: (v) => `${v.toFixed(1)} g/kg` },
  { field: "nitrogen", label: "Nitrogen", format: (v) => `${v.toFixed(2)} g/kg` },
  { field: "bulkDensity", label: "Bulk Density", format: (v) => `${v.toFixed(2)} g/cm³` },
  { field: "cec", label: "CEC", format: (v) => `${v.toFixed(1)} cmol/kg` },
  { field: "ocd", label: "OCD", format: (v) => `${v.toFixed(1)} kg/m³` },
];

const EROSION_CLASSES: ErosionClass[] = ["very_low", "low", "moderate", "high", "very_high"];
const EROSION_CLASS_LABELS: Record<ErosionClass, string> = {
  very_low: "Very Low",
  low: "Low",
  moderate: "Moderate",
  high: "High",
  very_high: "Very High",
};

const CARBON_CLASSES: CarbonClass[] = ["very_low", "low", "medium", "high", "very_high"];
const CARBON_CLASS_LABELS: Record<CarbonClass, string> = {
  very_low: "Very Low (<0.2 tC/ha/yr)",
  low: "Low (0.2–0.5 tC/ha/yr)",
  medium: "Medium (0.5–1.0 tC/ha/yr)",
  high: "High (1.0–2.0 tC/ha/yr)",
  very_high: "Very High (>2.0 tC/ha/yr)",
};

const INTERVENTION_LABELS: Record<InterventionType, string> = {
  reforestation: "Reforestation",
  silvopasture: "Silvopasture",
  cover_cropping: "Cover Cropping",
  biochar: "Biochar",
  keyline: "Keyline Design",
};

function ColorLegendRow({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-4 h-3 rounded-sm shrink-0" style={{ backgroundColor: color }} />
      <span className="text-[hsl(var(--muted-foreground))] text-xs">{label}</span>
    </div>
  );
}

/**
 * One ERA5-Land soil field's switch, depth selector, honesty notes and legend.
 *
 * One component rendered once per measure rather than two hand-written blocks: the two
 * fields share every caption a reader depends on -- that a zoomed-out view is a smoothed
 * average, that the day drawn is not always the day asked for, that blank ground is missing
 * coverage -- and a second copy would be a second place for those to rot. Everything that
 * genuinely differs comes from `soilFieldMeasureDefinition`.
 *
 * It owns its own query rather than taking the collection as a prop, on the same key the map
 * uses, so expanding this section never issues a request the map was not already making.
 */
function SoilFieldSection({
  measure,
  visible,
  bbox,
  zoom,
}: {
  measure: SoilFieldMeasure;
  visible: boolean;
  bbox?: string;
  zoom?: number;
}) {
  const definition = soilFieldMeasureDefinition(measure);
  const depth = useSoilStore((state) => state.fieldDepth[measure]);
  const setFieldDepth = useSoilStore((state) => state.setFieldDepth);
  // This measure's OWN row's settled day, read here rather than passed down from the parent.
  // The parent renders these in a `.map()`, so a hook per measure is only reachable from
  // inside the component -- and it has to be per measure, because the three fields are three
  // toggles with three sliders and scrubbing moisture back a week says nothing about
  // temperature. Same day LayerManager sends for the same toggle, so the section and the map
  // share one react-query entry.
  const { requestDate } = useDebouncedLayerDay(definition.toggleId);

  // `visible` alone, where this read used to be `open && visible`: the section is mounted
  // only while the dock has it expanded, so mounting is what "open" means now.
  const query = useSoilFieldQuery(bbox, {
    enabled: visible,
    measure,
    date: requestDate,
    depth,
    zoom,
  });
  const field = query.data;
  const aggregated = field !== undefined && field.granularity !== "detail";
  // The archive ends before the live edge, so "the day you asked for" and "the day drawn"
  // routinely differ. Saying so is the whole point: a field silently drawn from four months
  // ago while the slider reads today is a lie the map cannot tell on its own.
  const dayDiffers = field?.observedDay != null && field.observedDay !== field.requestedDay;
  // Defaulted rather than dereferenced. The server always sends the band table, but a
  // response replayed from IndexedDB was serialized by whatever schema was current when it
  // was written -- and a panel that throws is a worse failure than a missing legend.
  const bands = field?.bands ?? [];
  // The same rule as `bands`, for the same reason: `support` was added to this response on
  // 2026-09-02, and `query-persister.ts` replays a soil-field response written before that
  // straight out of IndexedDB. Dereferencing it there throws inside render and takes the whole
  // panel down, so the cell size is read optionally and the sentence that names it is only
  // written when there is a number to write.
  const servedCellWidthDegrees = field?.support?.cellWidthDegrees ?? null;

  return (
    <>
      {/* The switch lives in the dock's Soil layer rows, not here -- this section is what the
          layer says once it is on. Nothing renders while it is off: a depth picker and a band
          legend for a layer nobody is drawing describe nothing. */}
      {visible && (
        <div className="mt-1.5 flex flex-col gap-1.5">
          <h4 className="text-xs font-semibold text-[hsl(var(--foreground))]">
            {definition.layerLabel}
          </h4>
          <div className="flex flex-col gap-1.5">
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              Depth (ECMWF soil layer)
            </p>
            <div className="flex flex-wrap gap-1.5">
              {definition.depths.map((option) => (
                <button
                  key={option.depth}
                  className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                    depth === option.depth
                      ? "bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] border-transparent"
                      : "border-[hsl(var(--border))] text-[hsl(var(--foreground))] bg-[hsl(var(--card))]"
                  }`}
                  onClick={() => setFieldDepth(measure, option.depth)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          {/* `isFetching`, never `isLoading`. This read holds the previous answer while the
              next loads (`keepPreviousData`, see useViewportProxiedLayers), which sets
              `status: "success"` — so `isLoading` is permanently false after the first success
              and this line would never appear again for any later day or viewport. */}
          {query.isFetching && !query.isPlaceholderData && (
            <p
              role="status"
              aria-live="polite"
              className="text-xs text-[hsl(var(--muted-foreground))]"
            >
              Loading the {definition.fieldLabel} field for this view…
            </p>
          )}

          {/* The retained case, and it must be said before anything below it: every figure and
              every day in this section is read off `field`, which is the PREVIOUS request's
              answer while this is true. Worded without "loading" because offline pauses a fetch
              rather than cancelling it, and a retained frame can stand with nothing in flight. */}
          {query.isPlaceholderData && (
            <p
              role="status"
              aria-live="polite"
              className="text-xs text-[hsl(var(--muted-foreground))]"
            >
              The {definition.fieldLabel} figures below describe the previous request; this one
              has not arrived yet.
            </p>
          )}

          {/* Zoomed out past the detail tier. Captioned from the SERVED support -- the rung
              and the cell size the response declares -- rather than from the old
              `latticeDegrees`, which reported the LADDER's grid (0.01 at z9, 0.2 at z5) and so
              described a footprint finer than the quarter-degree measurement it was drawn from.
              "Smoothed contours" went with it: every rung is a complete tessellation of cells
              now, not an isoband trace. Shown whenever the response says so, never guessed. */}
          {aggregated && field && servedCellWidthDegrees !== null && (
            <p
              role="status"
              aria-live="polite"
              className="rounded-md border border-sky-500/40 bg-sky-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              Zoomed out to the z{field.zoomTier} rung: {field.cellCount}{" "}
              {servedCellWidthDegrees}° cell
              {field.cellCount === 1 ? "" : "s"} at this zoom.{" "}
              {servedCellWidthDegrees > SOIL_FIELD_MEASURED_CELL_DEGREES
                ? "Each is the mean of every measured 0.25° reading inside it, not an individual reading."
                : "Still the measured 0.25° grain, re-floored onto this rung's grid."}{" "}
              Zoom in for the detail rung.
            </p>
          )}

          {/* The archive's last day is not today's day. Naming both is the only way the
              map can be read correctly. */}
          {dayDiffers && field && (
            <p
              role="status"
              aria-live="polite"
              className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              Drawn for {field.observedDay}, the newest reading at or before{" "}
              {field.requestedDay} — nothing is carried forward past{" "}
              {field.maxObservationAgeDays} days.
            </p>
          )}

          {field?.reason === "stale" && (
            <p
              role="status"
              aria-live="polite"
              className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              Nothing is drawn for {field.requestedDay}: the newest ERA5-Land reading for this
              view is {field.newestAvailableDay}, more than {field.maxObservationAgeDays} days
              earlier. Scrub the time slider to {field.newestAvailableDay} or before to see the
              field.
            </p>
          )}

          {field?.reason === "not_published" && (
            <p
              role="status"
              aria-live="polite"
              className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              The ERA5-Land {definition.fieldLabel} lane does not cover this view yet. Blank
              ground here is missing coverage on our side, not {definition.blankGroundMisreading}
              .
            </p>
          )}

          {field?.reason === "not_forecastable" && (
            <p
              role="status"
              aria-live="polite"
              className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              {field.requestedDay} is in the future. ERA5-Land is a reanalysis archive, so
              there is nothing to draw and nothing may be invented for it.
            </p>
          )}

          {query.isError && (
            <p
              role="alert"
              className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              The {definition.fieldLabel} field could not be loaded for this view. Try again
              shortly.
            </p>
          )}

          {field?.availability === "published" && bands.length > 0 && (
            <>
              <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                  {definition.quantityLabel} ({definition.unitLabel}) —{" "}
                  {soilFieldDepthDefinition(measure, depth).label}
                </p>
                <div className="flex flex-col gap-1">
                  {bands.map((band) => (
                    <ColorLegendRow key={band.bandIndex} color={band.color} label={band.label} />
                  ))}
                </div>
              </div>
              <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                {field.attribution}
              </p>
            </>
          )}
        </div>
      )}
    </>
  );
}

// Reuses EROSION_COLORS (see erosion.ts) so risk color never disagrees between panels;
// suitability has one fewer tier than erosion, so very_high is unused here.
function SuitabilityBar({ score }: { score: number }) {
  const color =
    score >= 75
      ? EROSION_COLORS.very_low
      : score >= 50
      ? EROSION_COLORS.low
      : score >= 30
      ? EROSION_COLORS.moderate
      : EROSION_COLORS.high;
  return (
    <div className="flex items-center gap-2 flex-1">
      <div className="flex-1 h-2 rounded-full bg-[hsl(var(--muted))] overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${score}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-xs font-medium w-8 text-right" style={{ color }}>
        {score}
      </span>
    </div>
  );
}

/**
 * What the soil layers are showing, as the Soil section of the map dock.
 *
 * Every `enabled` flag lost its `open &&` term in the 2026-08-08 merge: this component is
 * mounted only while its dock section is expanded, so mounting IS being open, and a collapsed
 * section issues nothing. The four `<LayerToggle>` rows it carried went with the sheet -- the
 * section's own layer rows are the switches now.
 */
export function SoilDetails({
  queryPoint,
  onClearQueryPoint,
  bbox,
  zoom,
}: SoilDetailsProps) {
  const { property: selectedProperty, setProperty } = useSoilStore();

  const soilQuery = trpc.environmental.getSoilProperties.useQuery(
    { lat: queryPoint?.lat ?? 0, lon: queryPoint?.lon ?? 0 },
    { enabled: !!queryPoint }
  );

  const suitabilityQuery = trpc.environmental.getInterventionSuitability.useQuery(
    { lat: queryPoint?.lat ?? 0, lon: queryPoint?.lon ?? 0 },
    { enabled: !!queryPoint }
  );

  function handlePropertyChange(prop: SoilProperty) {
    setProperty(prop);
  }

  // The SSURGO collection the map is drawing, read back so this section can say when what
  // is drawn is partial or missing. The same hook LayerManager calls, on the bbox the dock
  // derived once, so the two observers share one react-query entry: expanding this section
  // never issues an upstream request the map was not already making.
  // `useLayerVisibility` and not `useLayerToggle`: this section must not become the sole
  // requester of a layer governance withholds from the map.
  const layerVisibility = useLayerVisibility();
  const soilSurveyVisible = layerVisibility["soil-survey"];
  const soilSurveyQuery = useSoilSurveyQuery(bbox, {
    enabled: soilSurveyVisible,
    zoom,
  });

  // No day is read here any more. The ERA5-Land sections below each take their OWN layer row's
  // day, inside `SoilFieldSection` -- a single day threaded down from this component would be
  // the global slider's shape surviving in a place that now has three independent ones. This
  // component's own feed, the SSURGO survey above, carries no date at all.
  const soilSurvey = soilSurveyQuery.data;
  // USDA holds more map units than it serves for one view and returned a subset; the
  // count below then describes part of the view, not the view.
  const soilSurveyTruncated = soilSurvey?.truncated === true;
  // A provider fault, not a coverage answer: `features: []` here means SDA did not
  // answer, which the map paints exactly like ground the survey found nothing on.
  const soilSurveyUnavailable = soilSurvey?.availability === "unavailable";
  const soilSurveyCount = soilSurvey?.features.length ?? 0;
  // Map units SDA did serve whose geometry this reader could not parse. They are dropped
  // rather than drawn at a guessed outline, so the ground under them paints blank -- a
  // gap in what we can read, which must never be captioned as unsurveyed ground.
  const soilSurveyUnreadable = soilSurvey?.unreadableGeometries ?? 0;
  // Undefined reads as "detail" so every pre-zoom-aware test fixture keeps its original
  // wording unchanged.
  const soilSurveyGranularity = soilSurvey?.granularity ?? "detail";
  // Ground nobody has fetched from USDA yet. It draws exactly like ground the survey
  // found nothing on, so this is the only surface that can tell the two apart -- the same
  // honest-gap duty `truncated` and `unreadableGeometries` carry. Undefined (a fixture or
  // a response from before persistence landed) claims no gap rather than inventing one.
  const soilSurveyCoverage = soilSurvey?.coverage;
  const soilSurveyUncoveredCells =
    soilSurveyCoverage === undefined
      ? 0
      : Math.max(soilSurveyCoverage.cells - soilSurveyCoverage.covered, 0);
  const soilSurveyAggregated = soilSurveyGranularity !== "detail";
  // The real SSURGO map units merged behind the drawn averages, summed across every
  // averaged region in view -- the count that keeps an average from reading as a
  // surveyed unit.
  const soilSurveyAggregatedUnitCount = soilSurveyAggregated
    ? (soilSurvey?.features ?? []).reduce((total, feature) => {
        const count = Number(
          (feature.properties as { mapUnitCount?: unknown } | null)?.mapUnitCount
        );
        return total + (Number.isFinite(count) ? count : 0);
      }, 0)
    : 0;

  const soil = soilQuery.data;
  const suitability = suitabilityQuery.data;
  const suitabilityAvailable = suitability?.availability === "published";

  return (
    <div className="flex flex-col">
      {/* The two ERA5-Land soil fields, moisture and temperature, off one component.
          Rendered unconditionally so each owns its own query on the same key the map uses;
          each gates its own body on its own switch in the dock's layer rows. */}
      {SOIL_FIELD_MEASURE_IDS.map((measure) => (
        <SoilFieldSection
          key={measure}
          measure={measure}
          visible={layerVisibility[soilFieldMeasureDefinition(measure).toggleId]}
          bbox={bbox}
          zoom={zoom}
        />
      ))}

      {/* What the survey layer is actually drawing. Both dishonest cases -- a subset
          served past USDA's row ceiling, and an upstream fault -- reach the map as
          polygons that stop, indistinguishable from ground the survey found nothing on,
          so this section is the only surface that can tell them apart. Same treatment
          WaterDetails gives the watershed collection. The registry's unavailableReason
          channel cannot carry this: soil-survey is upstream-proxied with no
          warehouseLayerName, so it has no per-day capability to be unavailable at, and
          a reason there would also disable the switch while the layer is still drawing. */}
      {soilSurveyVisible && (
        <div className="mt-1.5 flex flex-col gap-1.5">
          {/* `isFetching`, never `isLoading` -- see the field section above for why. */}
          {soilSurveyQuery.isFetching && !soilSurveyQuery.isPlaceholderData && (
            <p
              role="status"
              aria-live="polite"
              className="text-xs text-[hsl(var(--muted-foreground))]"
            >
              Loading the USDA soil survey for this view…
            </p>
          )}

          {/* Every count below -- "N SSURGO map units drawn for this view", the uncovered-cell
              and unreadable tallies -- is measured on the response in hand, which while this is
              true is the answer for the PREVIOUS viewport. Pan Boise to Portland and the survey
              keeps its old figures for the length of a USDA round trip; stating them as current
              is the same defect the map lane refused to introduce into the watershed LIST. */}
          {soilSurveyQuery.isPlaceholderData && (
            <p
              role="status"
              aria-live="polite"
              className="text-xs text-[hsl(var(--muted-foreground))]"
            >
              The survey figures below describe the previous view; this one has not arrived yet.
            </p>
          )}

          {/* Zoomed out past the detail tier: the shapes below are real map units
              merged by drainage class, not individual surveyed boundaries -- see
              usda-soil.ts §soil-survey-zoom. Shown whenever the response says so,
              never guessed ahead of it. */}
          {soilSurveyAggregated && (
            <p
              role="status"
              aria-live="polite"
              className="rounded-md border border-sky-500/40 bg-sky-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              Zoomed out: showing drainage-class averages built from{" "}
              {soilSurveyAggregatedUnitCount} real SSURGO map unit
              {soilSurveyAggregatedUnitCount === 1 ? "" : "s"}
              {soilSurveyGranularity === "coarse-average" ? ", generalized further" : ""}
              . These are not individual surveyed boundaries — zoom in for the exact
              survey.
            </p>
          )}

          {soilSurveyUnavailable && (
            <p
              role="status"
              aria-live="polite"
              className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              USDA Soil Data Access did not return a map-unit table for this view.
              Nothing is drawn — that is a provider fault, not an absence of soil. The
              view was not recorded as covered, so returning to it asks USDA again.
            </p>
          )}

          {soilSurveyTruncated && (
            <p
              role="status"
              aria-live="polite"
              className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              {soilSurveyAggregated ? (
                <>
                  This average covers only part of the visible map, centered on your
                  view — the sampled area is a subset, so ground left blank here is
                  not necessarily unsurveyed. Zoom in for wider or fully detailed
                  coverage.
                </>
              ) : (
                <>
                  More map units are stored for this view than it draws. The first{" "}
                  {soilSurveyCount} are shown — what is drawn is a subset, so ground
                  left blank here is not necessarily unsurveyed. Zoom in for complete
                  coverage.
                </>
              )}
            </p>
          )}

          {/* Ground the warehouse has never fetched from USDA. The one dishonest-empty
              case persistence introduced, and the most important note here: without it,
              un-backfilled ground reads as "USDA reports no surveyed SSURGO map units
              in this view". */}
          {!soilSurveyUnavailable && soilSurveyUncoveredCells > 0 && (
            <p
              role="status"
              aria-live="polite"
              className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              {soilSurveyUncoveredCells} of {soilSurveyCoverage?.cells} grid cell
              {soilSurveyCoverage?.cells === 1 ? "" : "s"} in this view have not been
              loaded from USDA yet, so blank ground there is missing coverage on our
              side, not an absence of soil.
            </p>
          )}

          {/* Rows SDA served that never became stored map units -- unreadable geometry,
              or a survey area with no publisher vintage to date the version by. Same
              honest-gap principle as `truncated`: the map paints the ground under them
              exactly like ground the survey found nothing on. */}
          {soilSurveyUnreadable > 0 && (
            <p
              role="status"
              aria-live="polite"
              className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              USDA returned {soilSurveyUnreadable} map unit
              {soilSurveyUnreadable === 1 ? "" : "s"} this reader could not store, so{" "}
              {soilSurveyUnreadable === 1 ? "it is" : "they are"} not drawn. Blank ground
              here is a gap in what we could read, not an absence of soil.
            </p>
          )}

          {/* The viewport area ceiling lives on the server, so the client learns it has
              been exceeded only from the rejected request; a transient SDA outage
              arrives the same way, hence one note covering both. */}
          {soilSurveyQuery.isError && (
            <p
              role="alert"
              className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
            >
              The soil survey could not be loaded for this view. It is served only over
              a bounded viewport area — zoom in, or try again shortly.
            </p>
          )}

          {/* Only claimed once a response actually arrived, and only for a published
              one: an undefined result is "not answered yet", not "USDA says there is
              nothing here". */}
          {soilSurvey && !soilSurveyUnavailable && !soilSurveyTruncated && (
            <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
              {soilSurveyCount > 0
                ? soilSurveyAggregated
                  ? `${soilSurveyCount} averaged drainage region${soilSurveyCount === 1 ? "" : "s"} shown for this view.`
                  : `${soilSurveyCount} SSURGO map units drawn for this view.`
                : soilSurveyUnreadable > 0
                  ? "No map unit USDA returned for this view could be read, so nothing is drawn."
                  : "USDA reports no surveyed SSURGO map units in this view."}
            </p>
          )}
        </div>
      )}

      {/* No scroller of its own: the dock's body is the one scrolling element -- see
          panel-scroll.ts rule 2, whose exhibit was this very wrapper. */}
      <div className="mt-4">
        <Tabs defaultValue="properties">
          <TabsList className="w-full">
            <TabsTrigger value="properties" className="flex-1 text-xs">
              <Layers className="h-3.5 w-3.5 mr-1" />
              Soil
            </TabsTrigger>
            <TabsTrigger value="erosion" className="flex-1 text-xs">
              <Wind className="h-3.5 w-3.5 mr-1" />
              Erosion
            </TabsTrigger>
            <TabsTrigger value="carbon" className="flex-1 text-xs">
              <Leaf className="h-3.5 w-3.5 mr-1" />
              Carbon
            </TabsTrigger>
          </TabsList>

          {/* Soil Properties Tab */}
          <TabsContent value="properties" className="flex flex-col gap-4 mt-4">
            <div className="flex flex-col gap-2">
              {/* "Display property" was a lie about scope. These buttons select nothing on the
                  map -- the SoilGrids raster they were built for cannot draw at all, because
                  `getEnvironmentalTileTemplate` returns "" until a first-party tile release
                  exists. What they DO drive is the point query below: which property the
                  clicked-point readout leads with. Named for that. */}
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                Highlight in point query
              </p>
              <div className="flex flex-wrap gap-1.5">
                {SOIL_PROPERTY_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                      selectedProperty === opt.value
                        ? "bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] border-transparent"
                        : "border-[hsl(var(--border))] text-[hsl(var(--foreground))] bg-[hsl(var(--card))]"
                    }`}
                    onClick={() => handlePropertyChange(opt.value)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Active property info */}
            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
              <p className="text-xs font-semibold mb-1 text-[hsl(var(--foreground))]">
                {SOIL_PROPERTY_LABELS[selectedProperty]}
              </p>
              {/* One sentence, not a ternary over a constant: ENVIRONMENTAL_TILES_CONFIGURED is
                  a literal `false` in src/lib/vegetation.ts, so the other branch was unreachable
                  copy describing a capability that has never existed. */}
              <p className="text-[10px] text-[hsl(var(--muted-foreground))] leading-relaxed">
                No soil raster is published, so nothing is drawn for this property. The values
                below come from ISRIC SoilGrids, read live for the point you click.
              </p>
            </div>

            {/* Queried point data. Capture is armed for as long as this section is mounted --
                the dock's Soil section owns it -- so the instruction below is always true
                here, and collapsing the section disarms it and drops the pin. */}
            {!queryPoint && (
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                Click anywhere on the map to query soil properties at that point. Click the
                pin again, or press Escape, to clear it.
              </p>
            )}

            {queryPoint && onClearQueryPoint && (
              <button
                className="self-start rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-2.5 py-1 text-xs font-medium text-[hsl(var(--foreground))] transition-colors hover:bg-[hsl(var(--accent))]"
                onClick={onClearQueryPoint}
              >
                Clear queried point
              </button>
            )}

            {queryPoint && soilQuery.isLoading && (
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                Querying SoilGrids…
              </p>
            )}

            {soil && queryPoint && (
              <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-2">
                <div>
                  <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                    Point ({queryPoint.lat.toFixed(4)}, {queryPoint.lon.toFixed(4)})
                  </p>
                  <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                    Showing {SOIL_PROPERTY_LABELS[selectedProperty]}, highlighted below.
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  {SOIL_POINT_FIELD_ROWS.map(({ field, label, format }) => {
                    const isSelected = SOIL_PROPERTY_POINT_FIELD[selectedProperty] === field;
                    return (
                      <Fragment key={field}>
                        <span
                          className={
                            isSelected
                              ? "font-semibold text-[hsl(var(--foreground))]"
                              : "text-[hsl(var(--muted-foreground))]"
                          }
                        >
                          {label}
                        </span>
                        <span
                          className={
                            isSelected
                              ? "font-semibold text-[hsl(var(--primary))]"
                              : "font-medium"
                          }
                        >
                          {format(soil[field])}
                        </span>
                      </Fragment>
                    );
                  })}
                </div>
              </div>
            )}
          </TabsContent>

          {/* Erosion Risk Tab */}
          <TabsContent value="erosion" className="flex flex-col gap-4 mt-4">
            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
              <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                Erosion Risk Classes
              </p>
              <div className="flex flex-col gap-1">
                {EROSION_CLASSES.map((cls) => (
                  <ColorLegendRow
                    key={cls}
                    color={EROSION_COLORS[cls]}
                    label={EROSION_CLASS_LABELS[cls]}
                  />
                ))}
              </div>
            </div>

            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-2">
              <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                USLE K-Factor Method
              </p>
              <p className="text-[10px] text-[hsl(var(--muted-foreground))] leading-relaxed">
                Erosion risk is estimated using the Universal Soil Loss Equation (USLE).
                K-factor is approximated from organic carbon and bulk density.
                LS-factor is derived from slope. C-factor is assigned by land cover type.
              </p>
            </div>

            {suitabilityAvailable && suitability.erosionClass && suitability.erosionRisk !== null && (
              <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-1">
                <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                  Point Erosion Risk
                </p>
                <div className="flex items-center gap-2 mt-1">
                  <span
                    className="w-3 h-3 rounded-full shrink-0"
                    style={{
                      backgroundColor:
                        EROSION_COLORS[suitability.erosionClass as ErosionClass] ?? "#888",
                    }}
                  />
                  <span className="text-xs font-medium capitalize">
                    {EROSION_CLASS_LABELS[suitability.erosionClass as ErosionClass] ??
                      suitability.erosionClass}{" "}
                    (score: {suitability.erosionRisk})
                  </span>
                </div>
              </div>
            )}
          </TabsContent>

          {/* Carbon Potential Tab */}
          <TabsContent value="carbon" className="flex flex-col gap-4 mt-4">
            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
              <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                Sequestration Potential
              </p>
              <div className="flex flex-col gap-1">
                {CARBON_CLASSES.map((cls) => (
                  <ColorLegendRow
                    key={cls}
                    color={CARBON_COLORS[cls]}
                    label={CARBON_CLASS_LABELS[cls]}
                  />
                ))}
              </div>
            </div>

            {!queryPoint && (
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                Click on the map to see intervention-specific carbon potential.
              </p>
            )}

            {queryPoint && suitabilityQuery.isLoading && (
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                Calculating intervention suitability…
              </p>
            )}

            {suitability?.availability === "unavailable" && (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                Intervention effects are unavailable until a validated,
                evidence-linked model release is published.
              </p>
            )}

            {/* Two very different failures, told apart by the code the router chose.
                PRECONDITION_FAILED is `SoilEvidenceUnavailableError` -- SoilGrids
                actually reporting no measurement at this cell. Anything else is a
                transport or provider fault. The previous single sentence blamed both on
                an unpublished warehouse release, which names neither cause: this point
                query has never been warehouse-backed, it reads ISRIC live. */}
            {queryPoint && soilQuery.isError && (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                {soilQuery.error?.data?.code === "PRECONDITION_FAILED"
                  ? "SoilGrids reports no soil measurement at this point."
                  : "SoilGrids did not answer for this point — that is a provider fault, not an absence of soil. Try again shortly."}
              </p>
            )}

            {suitabilityAvailable && (
              <>
                <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-2">
                  <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                    Intervention Suitability &amp; Carbon Potential
                  </p>
                  {(Object.entries(suitability.interventions) as [InterventionType, NonNullable<(typeof suitability.interventions)[InterventionType]>][]).map(
                    ([type, data]) => {
                      const carbonClass = classifyCarbonPotential(
                        data.carbonPotential.potentialGain
                      );
                      return (
                        <div
                          key={type}
                          className="border-t border-[hsl(var(--border))] pt-2 first:border-0 first:pt-0"
                        >
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-xs font-medium text-[hsl(var(--foreground))]">
                              {INTERVENTION_LABELS[type]}
                            </span>
                            <span
                              className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                              style={{
                                backgroundColor: CARBON_COLORS[carbonClass] + "33",
                                color: CARBON_COLORS[carbonClass],
                              }}
                            >
                              {data.carbonPotential.potentialGain.toFixed(2)} tC/ha/yr
                            </span>
                          </div>
                          <SuitabilityBar score={data.suitabilityScore} />
                          <p className="text-[10px] text-[hsl(var(--muted-foreground))] mt-1">
                            {data.rationale}
                          </p>
                          <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                            Confidence:{" "}
                            <span className="capitalize font-medium">
                              {data.carbonPotential.confidenceClass}
                            </span>{" "}
                            · ~{data.carbonPotential.yearsToSaturation} yrs to saturation
                          </p>
                        </div>
                      );
                    }
                  )}
                </div>
              </>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
