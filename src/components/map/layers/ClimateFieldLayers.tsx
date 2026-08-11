"use client";

import type { Map as MapLibreMap } from "maplibre-gl";
import { ClimateFieldLayer } from "@/components/map/layers/ClimateFieldLayer";
import { useClimateFieldQuery } from "@/hooks/useViewportProxiedLayers";
import {
  useClimateDisplayMode,
  useDebouncedLayerDay,
  useLayerOpacities,
  useLayerVisibility,
} from "@/lib/map/layer-toggle-context";
import {
  CLIMATE_FIELD_SIGNALS,
  CLIMATE_FIELD_SIGNAL_IDS,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";

/** Empty rather than null, so a switched-off row has something for `setData` to clear with. */
const EMPTY_FEATURE_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/**
 * The nine NASA POWER rows, each reading and drawing its own signal on its own day.
 *
 * A component per signal rather than a loop inside `LayerManager`, because every signal needs
 * its OWN `useDebouncedLayerDay` and its own `useClimateFieldQuery` -- two hooks each, nine
 * times over. Calling them in a loop up in `LayerManager` would work at runtime (the list is a
 * module constant, so hook order is stable) but it is exactly the shape the rules-of-hooks lint
 * forbids, and it would add eighteen hooks to a component that already carries thirty. One
 * child per signal is the idiomatic version of the same thing and keeps each signal's day,
 * query and paint together.
 *
 * Every child is mounted unconditionally and switched off rather than unmounted, for the reason
 * every other layer in this tree is: `ClimateFieldLayer` tears its own MapLibre layers down
 * when `visible` goes false, and unmounting on toggle would race a `style.load` re-attach. An
 * off row's query is disabled, so a switched-off signal costs a subscription and no request.
 */
export function ClimateFieldLayers({
  map,
  bbox,
}: {
  map: MapLibreMap | null;
  /**
   * The viewport, derived ONCE by `LayerManager` and passed down rather than re-derived here.
   * Nine `useViewportBounds()` calls would be nine chances to key nine queries on nine
   * marginally different bbox strings mid-pan, which is nine cache entries for one viewport.
   */
  bbox: string | null;
}) {
  return (
    <>
      {CLIMATE_FIELD_SIGNAL_IDS.map((signal) => (
        <ClimateSignalLayer key={signal} map={map} bbox={bbox} signal={signal} />
      ))}
    </>
  );
}

/**
 * One signal: its row's settled day, its own viewport read, and the form it is painted in.
 *
 * The day comes from THIS signal's toggle, never from a shared climate day. That is the whole
 * point of the nine rows -- a reader can hold precipitation on 2026-08-06 beside air
 * temperature on 2025-07-01 -- and reading another row's day here would draw one signal on
 * another's date while both legends still captioned themselves from their own collections.
 */
function ClimateSignalLayer({
  map,
  bbox,
  signal,
}: {
  map: MapLibreMap | null;
  bbox: string | null;
  signal: ClimateFieldSignalId;
}) {
  const { toggleId } = CLIMATE_FIELD_SIGNALS[signal];
  const layerVisibility = useLayerVisibility();
  const layerOpacity = useLayerOpacities();
  const climateMode = useClimateDisplayMode();
  const day = useDebouncedLayerDay(toggleId);
  const visible = layerVisibility[toggleId];
  const renderForm = climateMode.renderFormFor(signal);

  const query = useClimateFieldQuery(bbox, {
    enabled: visible,
    signal,
    variant: climateMode.airTemperatureVariant,
    date: day.requestDate,
    renderForm,
  });

  return (
    <ClimateFieldLayer
      map={map}
      signal={signal}
      renderForm={renderForm}
      geojson={query.data ?? EMPTY_FEATURE_COLLECTION}
      opacityScale={layerOpacity[toggleId]}
      visible={visible}
    />
  );
}
