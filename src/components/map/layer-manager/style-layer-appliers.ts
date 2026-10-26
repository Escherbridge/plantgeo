/**
 * The four writers of style-baked layer state -- source data, visibility, opacity and the date
 * filter -- plus the intervention-drafts source writer, as plain functions of a map and a value.
 * LayerManager wraps each in a `useCallback(fn, [])` and owns WHEN they run; this module owns only
 * WHAT they write. See src/components/map/AGENTS.md §LayerManager structure.
 */

import type maplibregl from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
import type { LayerVisibility } from "@/lib/map/layer-toggle-context";
import { scaleOpacityValue, styleLayerOpacityTargets } from "@/lib/map/layer-opacity";
import {
  LAYER_REGISTRY,
  styleBackedLayerEntries,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import {
  DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS,
  tileLayerDateFilter,
} from "@/lib/map/tile-layer-date-filter";
import {
  INTERVENTION_DRAFTS_SOURCE_ID,
  PARQUET_FEATURE_SOURCE_IDS,
  type ParquetFeatureSourceId,
} from "@/lib/map/sources";

/**
 * The style-baked tile toggles LayerManager holds a day for.
 *
 * `DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS` is typed `readonly LayerToggleId[]`, so it cannot
 * narrow anything on its own; this tuple is what makes LayerManager's `dateFilterableLayerDays` a
 * record the compiler checks against exactly the toggles a `useDebouncedLayerDay` call exists for.
 * A tuple with `satisfies` rather than a hand-written union, so the list and the type cannot
 * drift from each other -- there is now one place to add a toggle here instead of two.
 *
 * It can still drift from the EXPORTED constant, which is the drift that matters: an id added
 * there without a hook here would leave that layer with no filter at all, and an unfiltered
 * date-filterable layer draws its whole published record while its row's slider says otherwise
 * -- the map showing four years of perimeters under a control that reads one day. Two things
 * catch it: `applyDateFilter` reports the gap rather than skipping it silently, and the test
 * "filters every date-filterable tile layer on that layer's own day" walks the exported
 * constant, so the gap fails a case rather than shipping.
 */
export const DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE = [
  "fire-perimeters",
  "evacuation-zones",
  "burn-severity",
  "sensors",
] as const satisfies readonly LayerToggleId[];

export type DateFilterableToggleId = (typeof DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE)[number];

/** True once the two lists have been compared; the drift is structural, so one report is enough. */
let dateFilterableToggleDriftReported = false;

/**
 * Names any disagreement between the exported constant and the toggles wired here.
 *
 * Both directions are a defect and neither shows up as one. A toggle the constant lists and
 * this component holds no day for draws its whole published record under a row whose slider
 * claims a single day; a toggle wired here that the constant does not list has a day nobody
 * ever applies, so its slider moves and its map does not. Reported rather than thrown: a
 * console line in the browser and in the test run is enough to lose an hour to, whereas
 * throwing would take the whole map down for a filter.
 */
function reportDateFilterableToggleDrift(): void {
  if (dateFilterableToggleDriftReported) return;
  dateFilterableToggleDriftReported = true;
  const wiredHere = new Set<string>(DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE);
  for (const toggleId of DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS) {
    if (wiredHere.has(toggleId)) continue;
    console.error(
      `LayerManager holds no day for the date-filterable toggle "${toggleId}", so its style ` +
        `layers draw their whole published record with no upper bound while its row offers a ` +
        `day. Add a useDebouncedLayerDay("${toggleId}") call, an entry in ` +
        `DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE and one in dateFilterableLayerDays.`
    );
  }
  const listedAsFilterable = new Set<string>(DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS);
  for (const toggleId of DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE) {
    if (listedAsFilterable.has(toggleId)) continue;
    console.error(
      `LayerManager holds a day for "${toggleId}", which ` +
        `DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS does not list, so nothing ever applies it: ` +
        `that row's slider moves and its map does not.`
    );
  }
}

/**
 * The fourth applier, and the one the other three would be useless without: it writes the DATA a
 * style-baked layer has no component to hold for it.
 *
 * Guarded with `getSource` for the same reason `applyVisibility` guards with `getLayer`: running
 * against a half-built style must be a no-op per missing source rather than an error, and the
 * `styleReady` dependency on LayerManager's data effect is what makes the pass repeat once the
 * style catches up. Deliberately NOT in the `styledata` handler's frame with the filter and the
 * opacity: those rebuild an expression per layer, while this re-serializes up to 9,396 basins,
 * and `styledata` fires as every tile lands.
 */
export function applyParquetFeatureData(
  mapInstance: maplibregl.Map,
  collections: Record<ParquetFeatureSourceId, GeoJSON.FeatureCollection>
): void {
  for (const sourceId of PARQUET_FEATURE_SOURCE_IDS) {
    const source = mapInstance.getSource(sourceId);
    // Structural, not `instanceof`: a basemap swap can leave a same-named source of another
    // kind mid-rebuild, and calling setData on one would throw inside a style event handler.
    if (typeof (source as { setData?: unknown } | undefined)?.setData !== "function") continue;
    (source as GeoJSONSource).setData(collections[sourceId]);
  }
}

/**
 * The intervention-drafts source's own writer, kept separate from `applyParquetFeatureData`
 * above even though the mechanism (empty geojson source, filled by setData, re-applied on
 * style reload) is identical -- that source is explicitly NOT Parquet-fed (see sources.ts),
 * and folding it into `PARQUET_FEATURE_SOURCE_IDS` would misdescribe it for the next reader.
 * Guarded the same way: a missing/mid-rebuild source is a no-op, never a thrown error.
 */
export function applyInterventionDraftsData(
  mapInstance: maplibregl.Map,
  geojson: GeoJSON.FeatureCollection
): void {
  const source = mapInstance.getSource(INTERVENTION_DRAFTS_SOURCE_ID);
  if (typeof (source as { setData?: unknown } | undefined)?.setData !== "function") return;
  (source as GeoJSONSource).setData(geojson);
}

// Sync visibility of every style-baked layer (fire-perimeters, interventions, sensors,
// evacuation-zones, burn-severity, watersheds) with activeLayers -- these are declared in the
// style rather than mounted as React components, so they need setLayoutProperty instead of an
// unmount/remount cycle. Whether the layer's source is a Martin tile source or one of the
// Parquet-fed GeoJSON sources makes no difference here: this walks the registry.
export function applyVisibility(
  mapInstance: maplibregl.Map,
  currentVisibility: LayerVisibility
): void {
  for (const entry of styleBackedLayerEntries()) {
    const visibility = currentVisibility[entry.toggleId] ? "visible" : "none";
    for (const layerId of entry.styleLayerIds) {
      if (mapInstance.getLayer(layerId)) {
        mapInstance.setLayoutProperty(layerId, "visibility", visibility);
      }
    }
  }
}

// The opacity sibling of applyVisibility, and for the same reason: a style-baked layer has
// no React component to fold a multiplier into, so this is its single writer. It reaches
// ONLY the (layer, property) pairs styleLayerOpacityTargets() derives from the registry
// crossed with getLayers() -- basemap chrome, the service-area mask and every
// component-added layer are structurally out of reach. The value is always the AUTHORED
// base scaled, never an absolute, so re-running it is idempotent and a factor of 1 rewrites
// exactly what the style declared.
export function applyOpacity(
  mapInstance: maplibregl.Map,
  currentOpacity: Record<LayerToggleId, number>
): void {
  for (const target of styleLayerOpacityTargets()) {
    if (!mapInstance.getLayer(target.layerId)) continue;
    mapInstance.setPaintProperty(
      target.layerId,
      target.property,
      scaleOpacityValue(target.base, currentOpacity[target.toggleId] ?? 1)
    );
  }
}

// Puts each style-baked Martin layer on ITS OWN row's slider. They are not React-mounted, so
// they cannot take a date as a prop the way every layer below does; migration 0015 emits
// `observed_day` on each feature and this applies the matching style filter per layer.
// Re-filtering costs no requests -- the tiles are already in the browser -- so unlike the
// queries above this reads the SETTLED day only because there is no point re-running it per
// pointer tick.
//
// One filter per toggle rather than one fanned across all of them: `tileLayerDateFilter`
// always took a single day for a single layer, and fanning it was only ever the global
// slider's shape leaking down here.
//
// Every listed toggle is WRITTEN on every pass, including the ones that end up unfiltered.
// Clearing has to be as explicit as filtering: a basemap swap rebuilds each style layer from
// its authored spec, so "leave it alone" and "make sure it carries no day" are the same
// instruction only until the first swap -- and a layer that loses its selectable day (a
// capabilities payload that reclassifies it, or one that fails to arrive) would otherwise keep
// a filter nothing can move.
export function applyDateFilter(
  mapInstance: maplibregl.Map,
  days: Record<DateFilterableToggleId, string | null>
): void {
  reportDateFilterableToggleDrift();
  for (const toggleId of DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS) {
    // Three states, not two. A day filters; `null` -- no selectable day -- clears, which is
    // exactly what the layer drew before it had a slider at all; `undefined` is the wiring
    // gap `reportDateFilterableToggleDrift` just named, and clears too, because there is no
    // day to honour. Skipping it used to read as the cautious choice and was not: the layer
    // is rebuilt filterless on the next style load either way, so the gap's real consequence
    // is the whole published record under a row that claims one day.
    const day = (days as Record<string, string | null | undefined>)[toggleId];
    const filter = tileLayerDateFilter(day ?? null);
    for (const layerId of LAYER_REGISTRY[toggleId].styleLayerIds) {
      if (mapInstance.getLayer(layerId)) mapInstance.setFilter(layerId, filter ?? undefined);
    }
  }
}
