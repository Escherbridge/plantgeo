import { useMemo } from "react";
import {
  LAND_CONTEXT_GROUP_IDS,
  type LandContextFeature,
  type LandContextGroupId,
  type LandContextResultMeta,
  type LandContextSelectionInput,
} from "@/stores/land-context-store";

/**
 * PLACEHOLDER READER HOOK -- owned by a sibling worker, not this one.
 *
 * This worker (map/interaction layer) has EXCLUSIVE ownership of
 * `src/components/map/land-context/**` and `src/stores/land-context-store.ts` only. The real
 * `useLandContextQuery` belongs to whichever agent builds the tRPC reader against the
 * `pnw_land_context_reference_plane_20260911` reference plane (see that track's
 * `evidence/source-inventory.md` for admitted sources/versions). Until that lands, this stub:
 *
 * - returns an empty result set with honest, zeroed truncation/pagination metadata so nothing
 *   downstream can mistake "no stub data" for "admitted empty result" (spec: "Distinguish ...
 *   admitted empty result, partial selected-area coverage ... ").
 * - never fabricates parcel/utility/BLM/state records -- only a real reader has that authority.
 *
 * Swap this import for the real `@/lib/server/services/land-context` tRPC hook when available;
 * the call signature below (selection + enabled groups in, features + meta out) is the
 * intended shape for that integration.
 */
export interface UseLandContextQueryResult {
  data: LandContextFeature[];
  meta: LandContextResultMeta | null;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
}

export function useLandContextQuery(
  selection: LandContextSelectionInput | null,
  enabledGroups: Record<LandContextGroupId, boolean>
): UseLandContextQueryResult {
  const activeGroups = useMemo(
    () => LAND_CONTEXT_GROUP_IDS.filter((group) => enabledGroups[group]),
    [enabledGroups]
  );

  // TODO(sibling worker: land-context reference-plane reader): replace with the real tRPC
  // query, e.g. `api.landContext.queryIntersecting.useQuery({ selection, groups: activeGroups })`.
  // Intentionally not memoized against `selection`/`activeGroups` beyond this stub -- there is
  // no fetch to dedupe yet.
  void selection;
  void activeGroups;

  return {
    data: [],
    meta: null,
    isLoading: false,
    isError: false,
    error: null,
  };
}
