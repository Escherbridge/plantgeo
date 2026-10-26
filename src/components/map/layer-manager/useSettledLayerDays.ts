"use client";

/**
 * Every settled per-layer day LayerManager keys a read or a style filter on, plus whether each
 * style-baked tile toggle has a day a reader can actually choose. See src/components/map/AGENTS.md
 * §LayerManager structure.
 */

import { useDebouncedLayerDay } from "@/lib/map/layer-toggle-context";
import { hasSelectableDay, useTimeSliderStore } from "@/stores/time-slider-store";

export function useSettledLayerDays() {
  // One settled day per LAYER, never one for the map: since 2026-08-09 each row scrubs its own
  // axis and opens on its own `latestObservedDate`, so there is no map-wide day left to read.
  // `requestDate` is undefined whenever THAT layer's day is the server's today, which keeps the
  // hot path on the exact dateless query key -- and the exact server query -- it has always
  // used, so first paint never fetches the same day twice. Settled rather than raw: a
  // day-granular scrub writes on every pointer tick.
  //
  // One call per layer rather than a loop, for the same reason the three soil fields below are
  // three calls: hooks cannot be called from one. The upside over the single global read this
  // replaces is that a scrub on one row now re-runs one of these, not all of them.
  // No `fireDay` here: `useParquetFireDetections` reads the `fire` row's day itself and hands
  // back the settled one, so the map and `FireDetails` cannot key two entries for one answer.
  const droughtDay = useDebouncedLayerDay("drought");
  const waterDay = useDebouncedLayerDay("water");
  const vegetationDay = useDebouncedLayerDay("vegetation");
  const soilMoistureDay = useDebouncedLayerDay("soil-moisture");
  const soilTemperatureDay = useDebouncedLayerDay("soil-temperature");
  const soilVpdDay = useDebouncedLayerDay("soil-vpd");
  // No climate day here: the nine NASA POWER rows each settle their own inside
  // `ClimateFieldLayers`, which is the point of giving each signal its own slider.
  const weatherDay = useDebouncedLayerDay("weather");
  // The four style-baked tile toggles. They are read here alongside the component-mounted
  // layers rather than inside their own children because each is a ROW in the dock with a
  // slider of its own, exactly like the layers above -- being drawn by a style filter instead
  // of by a React component changes how the day is applied, not whose day it is.
  const firePerimetersDay = useDebouncedLayerDay("fire-perimeters");
  const evacuationZonesDay = useDebouncedLayerDay("evacuation-zones");
  const burnSeverityDay = useDebouncedLayerDay("burn-severity");
  const sensorsDay = useDebouncedLayerDay("sensors");

  // Whether each tile toggle has a day a user can actually CHOOSE -- the same question, asked of
  // the same function, that decides whether its row draws a slider (LayerRow.tsx). A layer with
  // no selectable day must carry no date filter: `sliderDomain` refuses a snapshot, so
  // evacuation-zones and sensors get no control. burn-severity is NOT one of them -- the read
  // model declares it `event` (environmental-read-model.ts, LAYER_TEMPORAL_KINDS), so it draws a
  // slider and must be filtered. It was in that list only while it was absent from that table and
  // inherited `snapshot`, which is why an unbounded MTBS layer used to draw every scar through
  // 2026 beneath perimeters scrubbed to 2024. Filtering a genuine snapshot anyway installed
  // `["<=", ["get","observed_day"], latestObservedDate]` on rows with nothing to change it with,
  // and `latestObservedDate` is by contract the newest day AT OR ABOVE the density floor -- so
  // through every partially-ingested live-edge day, which is the normal state of a running
  // ingest lane, every sensor and evacuation zone observed that day was filtered off the map
  // with no slider, no date and no caption to say why.
  //
  // Selected as BOOLEANS, so the five-minute capabilities poll re-runs the applier only when an
  // answer actually changes rather than on every fresh payload object.
  const firePerimetersHasSelectableDay = useTimeSliderStore((state) =>
    hasSelectableDay(state.capabilities, "fire-perimeters")
  );
  const evacuationZonesHasSelectableDay = useTimeSliderStore((state) =>
    hasSelectableDay(state.capabilities, "evacuation-zones")
  );
  const burnSeverityHasSelectableDay = useTimeSliderStore((state) =>
    hasSelectableDay(state.capabilities, "burn-severity")
  );
  const sensorsHasSelectableDay = useTimeSliderStore((state) =>
    hasSelectableDay(state.capabilities, "sensors")
  );

  return {
    droughtDay,
    waterDay,
    vegetationDay,
    soilMoistureDay,
    soilTemperatureDay,
    soilVpdDay,
    weatherDay,
    firePerimetersDay,
    evacuationZonesDay,
    burnSeverityDay,
    sensorsDay,
    firePerimetersHasSelectableDay,
    evacuationZonesHasSelectableDay,
    burnSeverityHasSelectableDay,
    sensorsHasSelectableDay,
  };
}

export type SettledLayerDays = ReturnType<typeof useSettledLayerDays>;
