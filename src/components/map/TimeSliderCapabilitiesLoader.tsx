"use client";

import { useEffect } from "react";
import { trpc } from "@/lib/trpc/client";
import { useTimeSliderStore } from "@/stores/time-slider-store";

/** Matches CAPABILITIES_CACHE_TTL_MS in environmental-read-model.ts, so a poll is a cache hit. */
const CAPABILITIES_REFRESH_MS = 5 * 60_000;

/**
 * The one read of `environmental.getSliderCapabilities`, and the one writer of the store fields
 * it feeds. Renders nothing.
 *
 * Headless and always mounted, deliberately. Every layer's day reaches every warehouse-backed
 * query on this map through `useDebouncedLayerDay(<toggle>)`, and those days exist only because
 * this payload does -- the server's `serverCurrentDate` is the ONLY definition of "today" here,
 * each layer's `latestObservedDate` is the day its row opens on, and neither has another source.
 * The scrubbers that draw them live on the layer rows in the left dock, which a reader can close
 * (and which unmounts entirely when closed), so the fetch cannot live inside it: closing the dock
 * would drop every layer's day out from under the whole map.
 *
 * ONE payload still feeds every row. Per-layer dates (2026-08-09) split the day, not the
 * capabilities: a fetch per slider would be one whole-warehouse scan per visible layer.
 *
 * Nothing else may set capabilities. That rule carries over from `TimeSliderPanel`, which owned
 * this fetch until 2026-08-08; before either existed the slider had no mount point at all, so
 * `setCapabilities` was called only from tests, no day ever left `UNINITIALIZED_DATE`, and both
 * server procedures were unreachable code. Keeping the read here is also what lets
 * `LayerTimeSlider` stay presentational enough to render against a fixture with no tRPC provider.
 *
 * There is no fallback domain, and there must not be one: a browser-clock guess would put
 * "today" on the wrong day for anyone outside UTC and invent an axis nobody measured. Until the
 * payload lands, every consumer renders nothing -- unless the fetch failed outright (see
 * `capabilitiesUnavailable`), because a failed request and a slow-but-healthy one must not look
 * identical: the former was mistaken for a UI bug once already (the read-model's
 * `invalid input syntax for type bigint: "0.01"` 500).
 */
export default function TimeSliderCapabilitiesLoader() {
  const setCapabilities = useTimeSliderStore((state) => state.setCapabilities);
  const setCapabilitiesUnavailable = useTimeSliderStore(
    (state) => state.setCapabilitiesUnavailable
  );

  // The layer list moves only when an ingest run lands a new day, so refetching on every
  // focus would spend the whole-warehouse scan for an answer that has not changed.
  //
  // But `serverCurrentDate` rides in the same payload and it DOES change, once a day, at an
  // instant no user action coincides with. This component is mounted inside MapView and never
  // unmounts, so without a timer a tab open across UTC midnight keeps reporting yesterday as
  // today: `sliderDomain` pins `lastDay` there, `clampDateToDomain` refuses to scrub past it,
  // the axis is labelled "Today <yesterday>", and anything ingested for the new day is
  // unreachable until reload. The interval bounds that staleness to CAPABILITIES_REFRESH_MS
  // and costs nothing upstream: `readLayerCapabilities` memoizes for the same 5 minutes behind
  // a single-flight guard, so the poll is a server-side cache hit that re-stamps the date.
  const capabilitiesQuery = trpc.environmental.getSliderCapabilities.useQuery(undefined, {
    staleTime: CAPABILITIES_REFRESH_MS,
    refetchInterval: CAPABILITIES_REFRESH_MS,
    refetchOnWindowFocus: false,
  });

  const capabilities = capabilitiesQuery.data;
  const isError = capabilitiesQuery.isError;

  useEffect(() => {
    if (capabilities === undefined) return;
    setCapabilities(capabilities);
  }, [capabilities, setCapabilities]);

  // Only "never got a payload at all" is worth interrupting the slider for. `data` stays the
  // last successful payload across a background refetch failure (react-query does not clear
  // it), so once one fetch has succeeded a later transient failure leaves this false and
  // TimeSlider keeps drawing from the stale-but-real capabilities already in the store.
  useEffect(() => {
    setCapabilitiesUnavailable(capabilities === undefined && isError);
  }, [capabilities, isError, setCapabilitiesUnavailable]);

  return null;
}
