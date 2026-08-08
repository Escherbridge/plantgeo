"use client";

import { useClimateFieldQuery } from "@/hooks/useViewportProxiedLayers";
import { useDebouncedMapDay, useLayerVisibility } from "@/lib/map/layer-toggle-context";
import { useClimateStore } from "@/stores/climate-store";
import {
  CLIMATE_FIELD_SIGNAL_IDS,
  climateFieldSignalDefinition,
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

/** The shared chip styling for the signal and statistic pickers. */
function pickerButtonClassName(selected: boolean): string {
  return `rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
    selected
      ? "bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] border-transparent"
      : "border-[hsl(var(--border))] text-[hsl(var(--foreground))] bg-[hsl(var(--card))]"
  }`;
}

/**
 * What the NASA POWER climate field is showing, as the Climate section of the map dock.
 *
 * A SIGNAL picker, not a second clock: the day always comes from the global time slider, as
 * `src/components/map/AGENTS.md` "One time control, projected per layer" requires. The
 * statistic row appears only for air temperature, which is the one signal NASA POWER
 * publishes three ways over the same cells and days.
 *
 * It owns its own query rather than taking the collection as a prop, on the same key the map
 * uses, so expanding this section never issues a request the map was not already making.
 */
export function ClimateDetails({ bbox }: ClimateDetailsProps) {
  // `useLayerVisibility` and not `useLayerToggle`: this section must not become the sole
  // requester of a layer governance withholds from the map.
  const layerVisibility = useLayerVisibility();
  const visible = layerVisibility["climate-field"];

  const signal = useClimateStore((state) => state.signal);
  const airTemperatureVariant = useClimateStore((state) => state.airTemperatureVariant);
  const setSignal = useClimateStore((state) => state.setSignal);
  const setAirTemperatureVariant = useClimateStore((state) => state.setAirTemperatureVariant);

  const definition = climateFieldSignalDefinition(signal);
  const { requestDate } = useDebouncedMapDay();

  const query = useClimateFieldQuery(bbox, {
    enabled: visible,
    signal,
    variant: airTemperatureVariant,
    date: requestDate,
  });
  // Read back only while the collection is the one the picker is describing.
  //
  // The server echoes `signal` for exactly this: react-query serves the previous key's data
  // for a frame after a signal switch, and every caption below -- the unit, the band labels,
  // the "does not cover this view" note -- is written from `definition`, which has already
  // moved. Without the guard a switch to Precipitation renders the air-temperature ramp under
  // a mm/day heading for one frame, which is a wrong reading rather than a slow one.
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

  // The switch lives in the dock's Climate layer rows, not here -- this section is what the
  // layer says once it is on. Nothing renders while it is off: a signal picker and a band
  // legend for a layer nobody is drawing describe nothing.
  if (!visible) {
    return (
      <p className="px-1 py-2 text-xs text-[hsl(var(--muted-foreground))]">
        Switch on the Climate layer above to pick a signal and read its legend.
      </p>
    );
  }

  return (
    <div className="mt-1.5 flex flex-col gap-1.5">
      <div className="flex flex-col gap-1.5">
        <p className="text-xs text-[hsl(var(--muted-foreground))]">
          Signal (NASA POWER daily)
        </p>
        <div className="flex flex-wrap gap-1.5">
          {CLIMATE_FIELD_SIGNAL_IDS.map((option) => (
            <button
              key={option}
              className={pickerButtonClassName(signal === option)}
              onClick={() => setSignal(option)}
            >
              {climateFieldSignalDefinition(option).label}
            </button>
          ))}
        </div>
      </div>

      {/* Only air temperature has statistics to choose between; the other eight signals
          publish one value per cell per day and would render an empty row. */}
      {definition.variants.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <p className="text-xs text-[hsl(var(--muted-foreground))]">Daily statistic</p>
          <div className="flex flex-wrap gap-1.5">
            {definition.variants.map((option) => (
              <button
                key={option.variant}
                className={pickerButtonClassName(airTemperatureVariant === option.variant)}
                onClick={() => setAirTemperatureVariant(option.variant)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Published for the signals whose coverage is a pilot rather than the lattice, so a
          nearly blank map reads as four measured cells and not as an outage. */}
      {definition.coverageNote !== null && (
        <p
          role="status"
          aria-live="polite"
          className="rounded-md border border-sky-500/40 bg-sky-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
        >
          {definition.coverageNote}
        </p>
      )}

      {/* `served && !field` is the signal-switch frame the guard above discards: the query has
          data, but for the signal the reader just navigated away from. Captioning it as
          loading keeps a sentence on screen instead of flashing an empty section. */}
      {(query.isLoading || (served !== undefined && field === undefined)) && (
        <p role="status" aria-live="polite" className="text-xs text-[hsl(var(--muted-foreground))]">
          Loading the {definition.fieldLabel} field for this view…
        </p>
      )}

      {/* The archive's last day is not today's day. Naming both is the only way the map can
          be read correctly. */}
      {dayDiffers && field && (
        <p
          role="status"
          aria-live="polite"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
        >
          Drawn for {field.observedDay}, the newest reading at or before {field.requestedDay} —
          nothing is carried forward past {field.maxObservationAgeDays} days.
        </p>
      )}

      {field?.reason === "stale" && (
        <p
          role="status"
          aria-live="polite"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
        >
          Nothing is drawn for {field.requestedDay}: the newest NASA POWER reading for this view
          is {field.newestAvailableDay}, more than {field.maxObservationAgeDays} days earlier.
          Scrub the time slider to {field.newestAvailableDay} or before to see the field.
        </p>
      )}

      {field?.reason === "not_published" && (
        <p
          role="status"
          aria-live="polite"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
        >
          The NASA POWER {definition.fieldLabel} lane does not cover this view. Blank ground here
          is missing coverage on our side, not {definition.blankGroundMisreading}.
        </p>
      )}

      {field?.reason === "not_forecastable" && (
        <p
          role="status"
          aria-live="polite"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
        >
          {field.requestedDay} is in the future. NASA POWER is an observation archive, so there
          is nothing to draw and nothing may be invented for it.
        </p>
      )}

      {/* More cells intersect the viewport than one response may carry, so the drawn field
          describes part of the view rather than the view. */}
      {field?.truncated === true && (
        <p
          role="status"
          aria-live="polite"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
        >
          Showing {field.cellCount} of the cells in this view, the most one response carries.
          Zoom in for the complete field.
        </p>
      )}

      {query.isError && (
        <p
          role="alert"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
        >
          The {definition.fieldLabel} field could not be loaded for this view. Try again shortly.
        </p>
      )}

      {field?.availability === "published" && bands.length > 0 && (
        <>
          <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
            <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
              {definition.quantityLabel} ({definition.unitLabel})
            </p>
            <p className="text-[10px] mb-2 text-[hsl(var(--muted-foreground))]">
              {field.cellCount} measured 0.5° cell{field.cellCount === 1 ? "" : "s"} drawn for{" "}
              {field.observedDay}.
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
    </div>
  );
}
