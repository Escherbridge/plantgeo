"use client";

import { useEffect } from "react";
import { useLandContextStore } from "@/stores/land-context-store";
import { useLandContextQuery } from "@/components/map/land-context/useLandContextQuery";

/**
 * Bridges the placeholder reader hook into the store. Mount this once alongside the toggles/
 * layer/panel components (the integrator decides where -- this worker does not own the map
 * shell). Re-fetches whenever the persistent `selection` or the enabled toggle groups change;
 * never on hover, since hover is transient per-feature state, not a new query.
 *
 * Cancellation-safety note: per spec ("Cancel superseded requests and prevent old geometry/
 * contact responses repainting a newer selection") the real reader must guard against
 * out-of-order responses. This stub has nothing to race yet (`useLandContextQuery` resolves
 * synchronously with empty data), but `setResults` here is deliberately the only writer, so
 * swapping in a real async hook only needs a request-id/AbortController check inside it.
 */
export function LandContextController() {
  const selection = useLandContextStore((state) => state.selection);
  const enabledGroups = useLandContextStore((state) => state.enabledGroups);
  const setResults = useLandContextStore((state) => state.setResults);

  const { data, meta, status } = useLandContextQuery(selection, enabledGroups);

  // `status` rides along so the store can tell a settled empty answer from one still in flight
  // (`LandContextStatusNotice` must never say "no features" mid-request); one writer, one call.
  useEffect(() => {
    setResults(data, meta, status);
  }, [data, meta, status, setResults]);

  return null;
}
