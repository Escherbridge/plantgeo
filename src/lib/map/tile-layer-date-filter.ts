import type { FilterSpecification } from "@maplibre/maplibre-gl-style-spec";
import { LAYER_REGISTRY, type LayerToggleId } from "@/lib/map/layer-registry";

/**
 * The MapLibre filter that puts style-baked tile layers on the time slider.
 *
 * These layers are Martin function sources baked into the style, not React-mounted readers,
 * so nothing in their path ever took a date: every published row in the tile envelope was
 * drawn at every point on a four-year axis. Migration 0015 puts the observation day on each
 * feature as `observed_day`; this turns the slider's day into the expression that reads it.
 *
 * Filtering in the style rather than in the tile query is what makes a scrub free. The tiles
 * already in the browser are re-filtered in place, so dragging across 1,400 days issues zero
 * requests -- where a dated tile URL would refetch every visible tile on every settled day.
 */

/** Layers whose tile function emits `observed_day`; the rest have no date to filter on. */
export const DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS: readonly LayerToggleId[] = [
  "fire-perimeters",
  "evacuation-zones",
  "burn-severity",
  "sensors",
];

/**
 * Features at or before `selectedDate`, plus every undated one.
 *
 * "At or before", not "on": these are event and snapshot layers. A fire perimeter mapped in
 * 2023 is still on the ground in 2024, and an evacuation zone published on a Tuesday is the
 * standing zone on the Wednesday. Showing only exact-day matches would empty the map on all
 * but the handful of days each layer happens to have a release on -- 26 days out of 1,460 for
 * evacuation zones -- which is the opposite of what a scrub back through time should show.
 *
 * Undated features are KEPT rather than hidden. `geo.feature_observation_day` returns null for
 * a row whose upstream timestamp it could not parse, and a row the warehouse cannot date is
 * not a row the warehouse has dated as future. Dropping it would silently delete data from the
 * map on the strength of a parse failure.
 *
 * String comparison is deliberate and safe: `observed_day` is emitted as "YYYY-MM-DD" text and
 * ISO-8601 dates sort lexicographically, so no date parsing enters the style at all.
 */
export function tileLayerDateFilter(selectedDate: string | null): FilterSpecification | null {
  if (selectedDate === null) return null;
  return [
    "any",
    // `!has` is the whole of the undated case, deliberately without a companion
    // ["==", ["get","observed_day"], null]. ST_AsMVT omits an attribute entirely when its
    // value is NULL, so there is no null to compare against -- and `==` against a null
    // literal is the kind of expression MapLibre can reject outright at style-parse time,
    // which would drop the filter and silently restore the undated behaviour this replaces.
    ["!", ["has", "observed_day"]],
    ["<=", ["get", "observed_day"], selectedDate],
  ] as FilterSpecification;
}

/** The style layer ids a date filter applies to, across every date-filterable toggle. */
export function dateFilterableStyleLayerIds(): string[] {
  return DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS.flatMap(
    (toggleId) => LAYER_REGISTRY[toggleId].styleLayerIds
  );
}
