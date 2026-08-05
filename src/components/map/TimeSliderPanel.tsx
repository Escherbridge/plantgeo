"use client";

import { useEffect } from "react";
import { trpc } from "@/lib/trpc/client";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import TimeSlider from "./TimeSlider";

/**
 * The connected time slider: the one place `environmental.getSliderCapabilities` is read and
 * fed into the store.
 *
 * Nothing else may set capabilities. Before this existed the slider had no mount point at
 * all, so `setCapabilities` was called only from tests, the store's `selectedDate` never left
 * `UNINITIALIZED_DATE`, and both server procedures were unreachable code. TimeSlider itself
 * stays presentational so it can be rendered against a fixture without a tRPC provider.
 *
 * The server's payload is the ONLY source of the axis and of "today"; there is no fallback
 * domain, because a browser-clock guess would put "today" on the wrong day for anyone outside
 * UTC and invent an axis nobody measured. Until the payload lands, TimeSlider renders nothing
 * -- unless the fetch failed outright (see capabilitiesUnavailable below), because a failed
 * request and a slow-but-healthy one must not look identical: the former was mistaken for a
 * UI bug once already (the read-model's `invalid input syntax for type bigint: "0.01"` 500).
 */
/** Matches CAPABILITIES_CACHE_TTL_MS in environmental-read-model.ts, so a poll is a cache hit. */
const CAPABILITIES_REFRESH_MS = 5 * 60_000;

export default function TimeSliderPanel() {
  const setCapabilities = useTimeSliderStore((state) => state.setCapabilities);
  const setCapabilitiesUnavailable = useTimeSliderStore(
    (state) => state.setCapabilitiesUnavailable
  );
  // The layer list moves only when an ingest run lands a new day, so refetching on every
  // focus would spend the whole-warehouse scan for an answer that has not changed.
  //
  // But `serverCurrentDate` rides in the same payload and it DOES change, once a day, at an
  // instant no user action coincides with. This panel is mounted inside MapView and never
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

  // Only "never got a payload at all" is worth interrupting the panel for. `data` stays the
  // last successful payload across a background refetch failure (react-query does not clear
  // it), so once one fetch has succeeded a later transient failure leaves this false and
  // TimeSlider keeps drawing from the stale-but-real capabilities already in the store.
  useEffect(() => {
    setCapabilitiesUnavailable(capabilities === undefined && isError);
  }, [capabilities, isError, setCapabilitiesUnavailable]);

  return <TimeSlider />;
}
