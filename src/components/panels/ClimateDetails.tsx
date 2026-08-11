"use client";

import { useClimateFieldQuery } from "@/hooks/useViewportProxiedLayers";
import {
  useClimateDisplayMode,
  useDebouncedLayerDay,
  useLayerVisibility,
} from "@/lib/map/layer-toggle-context";
import { useClimateStore } from "@/stores/climate-store";
import {
  CLIMATE_FIELD_SIGNALS,
  CLIMATE_FIELD_SIGNAL_IDS,
  CLIMATE_RENDER_FORM_LABELS,
  climateFieldSignalDefinition,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";

/**
 * No `zoom`, unlike `SoilDetailsProps`. Zoom selects the SSURGO survey's and the ERA5-Land
 * fields' server-side granularity and so keys their queries; the NASA POWER lane has one
 * serving tier, so a zoom here would put a value in the cache key that changes nothing about
 * the answer and split one entry into one per zoom level.
 */
interface ClimateDetailsProps {
  /** The map's viewport, handed down by the dock exactly as the other sections get it. */
  bbox?: string;
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
export function ClimateDetails({ bbox }: ClimateDetailsProps) {
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
        <ClimateSignalReport key={signal} signal={signal} bbox={bbox} />
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
}: {
  signal: ClimateFieldSignalId;
  bbox?: string;
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

  return (
    <section className="flex flex-col gap-1.5">
      <p className="text-xs font-semibold text-[hsl(var(--foreground))]">{definition.label}</p>

      {/* Offered only where the signal has more than one honest form. Precipitation and the
          three soil-wetness pilots withhold `isoline`, so their lists are shorter -- and a
          one-entry list would render a picker with nothing to pick. */}
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

      {(query.isLoading || (served !== undefined && field === undefined)) && (
        <p role="status" aria-live="polite" className="text-xs text-[hsl(var(--muted-foreground))]">
          Loading the {definition.fieldLabel} field for this view…
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
                eventually lie. */}
            <p className="text-[10px] mb-2 text-[hsl(var(--muted-foreground))]">
              {field.cellCount} of the {field.latticeCellCount} half-degree cells in this view
              carry a measurement for {field.observedDay}.
              {field.renderForm === "isoline" &&
                " Contours are interpolated between those cells; read values from the filled form."}
            </p>
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
