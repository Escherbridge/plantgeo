"use client";

import { useClimateFieldQuery } from "@/hooks/useViewportProxiedLayers";
import {
  useClimateDisplayMode,
  useDebouncedLayerDay,
  useLayerVisibility,
} from "@/lib/map/layer-toggle-context";
import { BASE_ZOOM_TIER } from "@/lib/map/zoom-tiers";
import { useClimateStore } from "@/stores/climate-store";
import {
  CLIMATE_FIELD_SIGNALS,
  CLIMATE_FIELD_SIGNAL_IDS,
  CLIMATE_RENDER_FORM_LABELS,
  climateFieldSignalDefinition,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";

/**
 * `zoom` exactly as `SoilDetailsProps` takes it, correcting the note that stood here: the claim
 * that "the NASA POWER lane has one serving tier" described the reader's hard-coded z13, not the
 * warehouse, which publishes z13/z9/z5/z0 for these lanes like every other. Zoom selects the one
 * physical rung that answers, so it keys the query -- and it must come from the SAME
 * `useViewportBounds()` derivation the map's own read uses, or this section and the map split
 * into two entries drawing two different aggregations of one viewport.
 */
interface ClimateDetailsProps {
  /** The map's viewport, handed down by the dock exactly as the other sections get it. */
  bbox?: string;
  /** The map's zoom, from the same derivation as `bbox`. Required: the procedure requires it. */
  zoom: number;
}

function ColorLegendRow({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-4 h-3 rounded-sm shrink-0" style={{ backgroundColor: color }} />
      <span className="text-[hsl(var(--muted-foreground))] text-xs">{label}</span>
    </div>
  );
}

/** The shared chip styling for the render-form and statistic pickers. */
function pickerButtonClassName(selected: boolean): string {
  return `rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
    selected
      ? "bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] border-transparent"
      : "border-[hsl(var(--border))] text-[hsl(var(--foreground))] bg-[hsl(var(--card))]"
  }`;
}

const NOTICE_CLASS_NAME =
  "rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]";

/**
 * What the NASA POWER lane is showing, as the Climate section of the map dock.
 *
 * NO SIGNAL PICKER since 2026-08-10, and its removal is the point. Each of the nine signals has
 * its own row in the dock's Climate group, its own switch, its own opacity and its own time
 * slider, so "which signal" is answered by the layer list -- the same way it is answered for
 * every other layer on the map. A picker here would be a second control over the same state,
 * and the two would disagree the moment a reader switched a row on.
 *
 * What is left is what a switch cannot say: the FORM each signal is painted in, the
 * air-temperature statistic, and the report each drawn signal owes its reader -- the day
 * actually drawn, the coverage, and the band table.
 */
export function ClimateDetails({ bbox, zoom }: ClimateDetailsProps) {
  // `useLayerVisibility` and not `useLayerToggle`: this section must not become the sole
  // requester of a layer governance withholds from the map.
  const layerVisibility = useLayerVisibility();
  const drawn = CLIMATE_FIELD_SIGNAL_IDS.filter(
    (signal) => layerVisibility[CLIMATE_FIELD_SIGNALS[signal].toggleId]
  );

  // Nothing renders while every row is off: a band legend for a layer nobody is drawing
  // describes nothing.
  if (drawn.length === 0) {
    return (
      <p className="px-1 py-2 text-xs text-[hsl(var(--muted-foreground))]">
        Switch on a climate layer above to read its legend. Each one carries its own date, so
        they need not be showing the same day.
      </p>
    );
  }

  return (
    <div className="mt-1.5 flex flex-col gap-3">
      {drawn.map((signal) => (
        <ClimateSignalReport key={signal} signal={signal} bbox={bbox} zoom={zoom} />
      ))}
    </div>
  );
}

/**
 * One switched-on signal's report: how it is drawn, on which day, over how much of the view.
 *
 * A component per signal because each owns a day and a query. Reading another signal's day here
 * would caption this one's legend with a date its own slider is not showing.
 */
function ClimateSignalReport({
  signal,
  bbox,
  zoom,
}: {
  signal: ClimateFieldSignalId;
  bbox?: string;
  zoom: number;
}) {
  const definition = climateFieldSignalDefinition(signal);
  const climateMode = useClimateDisplayMode();
  const setRenderForm = useClimateStore((state) => state.setRenderForm);
  const setAirTemperatureVariant = useClimateStore((state) => state.setAirTemperatureVariant);
  const renderForm = climateMode.renderFormFor(signal);

  // THIS signal's own settled day, which is the same one `ClimateFieldLayers` passes -- so this
  // section and the map land on one react-query entry rather than two that differ only in which
  // day they asked for.
  const { requestDate } = useDebouncedLayerDay(definition.toggleId);

  const query = useClimateFieldQuery(bbox, {
    enabled: true,
    signal,
    variant: climateMode.airTemperatureVariant,
    date: requestDate,
    renderForm,
    zoom,
  });
  // Read back only while the collection is the one this report is describing.
  //
  // The server echoes `signal` for exactly this: react-query serves a previous key's data for a
  // frame after a form or statistic change, and every caption below is written from
  // `definition`, which has already moved.
  const served = query.data;
  const field = served?.signal === signal ? served : undefined;
  // The archive ends before the live edge, so "the day you asked for" and "the day drawn"
  // routinely differ. Saying so is the whole point: a field silently drawn from months ago
  // while the slider reads today is a lie the map cannot tell on its own.
  const dayDiffers = field?.observedDay != null && field.observedDay !== field.requestedDay;
  // Defaulted rather than dereferenced. The server always sends the band table, but a
  // response replayed from IndexedDB was serialized by whatever schema was current when it
  // was written -- and a panel that throws is a worse failure than a missing legend.
  const bands = field?.bands ?? [];
  // The same rule as `bands`, for the same reason: `support` was added to this response on
  // 2026-09-02, and `query-persister.ts` replays a climate-field response written before that
  // straight out of IndexedDB. Dereferencing it there throws inside render and takes the whole
  // panel down, so the cell size is read optionally and the sentence that names it is only
  // written when there is a number to write.
  const servedCellWidthDegrees = field?.support?.cellWidthDegrees ?? null;

  return (
    <section className="flex flex-col gap-1.5">
      <p className="text-xs font-semibold text-[hsl(var(--foreground))]">{definition.label}</p>

      {/* Offered only where the signal has more than one honest form, and since 2026-09-02 six of
          the nine have exactly one. Precipitation and the three soil-wetness pilots withhold
          `isoline` (see `renderForms` in climate-field.ts), and `symbol` was withdrawn from all
          nine because the frozen render contract permits no point form for a continuous field --
          which leaves those six with nothing to pick between and no picker to draw. */}
      {definition.renderForms.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {definition.renderForms.map((form) => (
            <button
              key={form}
              className={pickerButtonClassName(renderForm === form)}
              onClick={() => setRenderForm(signal, form)}
            >
              {CLIMATE_RENDER_FORM_LABELS[form]}
            </button>
          ))}
        </div>
      )}

      {/* Only air temperature has statistics to choose between; the other eight signals
          publish one value per cell per day and would render an empty row. */}
      {definition.variants.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <p className="text-xs text-[hsl(var(--muted-foreground))]">Daily statistic</p>
          <div className="flex flex-wrap gap-1.5">
            {definition.variants.map((option) => (
              <button
                key={option.variant}
                className={pickerButtonClassName(
                  climateMode.airTemperatureVariant === option.variant
                )}
                onClick={() => setAirTemperatureVariant(option.variant)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Published for the signals whose coverage is a pilot rather than the lattice, so a
          nearly blank map reads as partial coverage and not as an outage. It states the KIND
          of coverage; the counted sentence in the legend below states the extent, measured on
          the same request that drew the cells. */}
      {definition.coverageNote !== null && (
        <p
          role="status"
          aria-live="polite"
          className="rounded-md border border-sky-500/40 bg-sky-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
        >
          {definition.coverageNote}
        </p>
      )}

      {/* `isFetching`, never `isLoading`. This read holds the previous answer while the next
          loads (`keepPreviousData`, see useViewportProxiedLayers), which sets `status: "success"`
          — so `isLoading` is permanently false after the first success and this line would never
          appear again. The `served`/`field` term beside it catches a form or statistic change,
          which is a different mismatch and not a date one. */}
      {((query.isFetching && !query.isPlaceholderData) ||
        (served !== undefined && field === undefined)) && (
        <p role="status" aria-live="polite" className="text-xs text-[hsl(var(--muted-foreground))]">
          Loading the {definition.fieldLabel} field for this view…
        </p>
      )}

      {/* The retained case. `field.requestedDay` below is the day the response in hand was asked
          for, so while this is true the "newest reading at or before {day}" note names the
          PREVIOUS request's day rather than this row's. Worded without "loading" because offline
          pauses a fetch rather than cancelling it. */}
      {query.isPlaceholderData && field !== undefined && (
        <p role="status" aria-live="polite" className="text-xs text-[hsl(var(--muted-foreground))]">
          The figures below describe the previous request; this one has not arrived yet.
        </p>
      )}

      {/* The archive's last day is not today's day. Naming both is the only way the map can
          be read correctly. */}
      {dayDiffers && field && (
        <p role="status" aria-live="polite" className={NOTICE_CLASS_NAME}>
          Drawn for {field.observedDay}, the newest reading at or before {field.requestedDay} —
          nothing is carried forward past {field.maxObservationAgeDays} days.
        </p>
      )}

      {field?.reason === "stale" && (
        <p role="status" aria-live="polite" className={NOTICE_CLASS_NAME}>
          Nothing is drawn for {field.requestedDay}: the newest NASA POWER reading for this view
          is {field.newestAvailableDay}, more than {field.maxObservationAgeDays} days earlier.
          Scrub this layer&apos;s slider to {field.newestAvailableDay} or before to see the field.
        </p>
      )}

      {field?.reason === "not_published" && (
        <p role="status" aria-live="polite" className={NOTICE_CLASS_NAME}>
          The NASA POWER {definition.fieldLabel} lane does not cover this view. Blank ground here
          is missing coverage on our side, not {definition.blankGroundMisreading}.
        </p>
      )}

      {field?.reason === "not_forecastable" && (
        <p role="status" aria-live="polite" className={NOTICE_CLASS_NAME}>
          {field.requestedDay} is in the future. NASA POWER is an observation archive, so there
          is nothing to draw and nothing may be invented for it.
        </p>
      )}

      {/* One degrade is left, and it now runs the other way. Wave 1 served POINTS for every form
          below z13; every rung is a filled tessellation now, and the only form the server may
          refuse is a contour at the DETAIL rung -- `LAYER_RENDER_CONTRACT` permits `isoband` at
          the coarse and middle bands only, because a band asserts the field varies smoothly
          between samples and the detail rung serves those samples themselves. Said here because
          the picker above still shows the requested chip selected: without this line the control
          reads as broken rather than as outranked by the zoom. */}
      {field !== undefined && field.renderForm !== renderForm && (
        <p role="status" aria-live="polite" className={NOTICE_CLASS_NAME}>
          Drawn as {CLIMATE_RENDER_FORM_LABELS[field.renderForm].toLowerCase()} at this zoom;
          zoom out for the requested {CLIMATE_RENDER_FORM_LABELS[renderForm].toLowerCase()} form.
        </p>
      )}

      {/* More cells intersect the viewport than one response may carry, so the drawn field
          describes part of the view rather than the view. */}
      {field?.truncated === true && (
        <p role="status" aria-live="polite" className={NOTICE_CLASS_NAME}>
          Showing {field.cellCount} of the cells in this view, the most one response carries.
          Zoom in for the complete field.
        </p>
      )}

      {query.isError && (
        <p role="alert" className={NOTICE_CLASS_NAME}>
          The {definition.fieldLabel} field could not be loaded for this view. Try again shortly.
        </p>
      )}

      {field?.availability === "published" && bands.length > 0 && (
        <>
          <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
            <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
              {definition.quantityLabel} ({definition.unitLabel})
            </p>
            {/* Both numbers come from the SAME response that drew the cells. The denominator
                used to be a constant in the bundle -- "4 of the lane's 397 cells" -- measured
                once against production and rendered directly above this live count until the
                backfill widened the pilot and the two openly contradicted each other on
                screen. A denominator that is not measured beside its numerator will always
                eventually lie.

                The CELL SIZE comes from the response too, off `support.cellWidthDegrees`, and is
                named rather than left to be inferred: one degree at z13/z9/z5 and five degrees at
                z0 are the same sentence with a hundredfold difference in what it claims about the
                ground. Wave 1 had no number to print below z13 -- it drew those rungs as points --
                and said "zoom in for the half-degree lattice", which described neither the lattice
                (one degree) nor what was drawn (dots). */}
            {servedCellWidthDegrees !== null && (
              <p className="text-[10px] mb-2 text-[hsl(var(--muted-foreground))]">
                {field.cellCount} of the {field.latticeCellCount}{" "}
                {servedCellWidthDegrees}° cells in this view carry a measurement for{" "}
                {field.observedDay}.
                {field.renderForm === "isoline" &&
                  " Bands are dissolved across those cells; read values from the filled form."}
                {field.zoomTier !== BASE_ZOOM_TIER &&
                  ` Served from the z${field.zoomTier} rung, which averages every measurement that fell in one cell.`}
              </p>
            )}
            <div className="flex flex-col gap-1">
              {bands.map((band) => (
                <ColorLegendRow key={band.bandIndex} color={band.color} label={band.label} />
              ))}
            </div>
          </div>
          <p className="text-[10px] text-[hsl(var(--muted-foreground))]">{field.attribution}</p>
        </>
      )}
    </section>
  );
}
