import { useMemo } from "react";
import { trpc } from "@/lib/trpc/client";
import {
  LAND_CONTEXT_GROUP_IDS,
  type LandContextFeature,
  type LandContextGroupId,
  type LandContextResultMeta,
  type LandContextSelectionInput,
} from "@/stores/land-context-store";
import type { LandContextResult } from "@/lib/environmental/land-context-contract";

/**
 * Reads the PNW land-context reference plane via the read-only
 * `landContext` tRPC router (see
 * `src/lib/server/trpc/routers/land-context.ts`) and reshapes its rich
 * per-result contract into the UI-facing `LandContextFeature` shape the
 * map/store/panel already consume.
 *
 * Every enabled group currently shares the same underlying boundary lookup
 * (point or bounded area); group-specific filtering happens client-side on
 * `sourceFeature.familyType` until the reference plane exposes a
 * group-scoped reader. `coverageState` values other than "matched" never
 * fabricate a feature -- see `toFeature` below.
 */
export interface UseLandContextQueryResult {
  data: LandContextFeature[];
  meta: LandContextResultMeta | null;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
}

const FAMILY_TO_GROUP: Record<string, LandContextGroupId> = {
  parcel: "parcels-land-use",
  land_use: "parcels-land-use",
  electric_service_territory: "electric-utility-territories",
  blm_surface_management: "blm-lands",
  state_managed_land: "state-managed-lands",
};

function groupForResult(result: LandContextResult): LandContextGroupId | null {
  const familyType = result.sourceFeature?.familyType;
  if (!familyType) return null;
  return FAMILY_TO_GROUP[familyType] ?? null;
}

function toFeature(result: LandContextResult, index: number): LandContextFeature | null {
  if (result.coverageState !== "matched" || !result.sourceFeature) return null;
  const group = groupForResult(result);
  if (!group) return null;

  return {
    id: `${result.sourceFeature.sourceNamespace}:${result.sourceFeature.nativeFeatureKey}:${index}`,
    group,
    title: result.organizationOffice?.officialPublicName ?? result.sourceFeature.nativeFeatureKey,
    category: result.sourceFeature.interestType,
    sourceVintage: result.sourceRelease?.sourceVersion,
    contactRouteSummary: result.documentedHelp ?? undefined,
    // Bounded readers do not yet return raw geometry (WKB decoding belongs to
    // the reference-plane reader, not this map-facing adapter); an empty
    // GeometryCollection keeps the feature listable/selectable without
    // fabricating a shape the source did not provide.
    geometry: { type: "GeometryCollection", geometries: [] },
    contactVerified: result.route?.status === "active",
  };
}

function toResults(
  results: LandContextResult[],
  enabledGroups: Record<LandContextGroupId, boolean>
): LandContextFeature[] {
  return results
    .map((result, index) => toFeature(result, index))
    .filter((feature): feature is LandContextFeature => feature !== null && enabledGroups[feature.group]);
}

export function useLandContextQuery(
  selection: LandContextSelectionInput | null,
  enabledGroups: Record<LandContextGroupId, boolean>
): UseLandContextQueryResult {
  const activeGroups = useMemo(
    () => LAND_CONTEXT_GROUP_IDS.filter((group) => enabledGroups[group]),
    [enabledGroups]
  );

  const hasActiveGroup = activeGroups.length > 0;
  const pointEnabled = Boolean(selection && selection.mode === "point" && selection.point && hasActiveGroup);
  const areaEnabled = Boolean(
    selection && selection.mode === "area" && selection.areaPolygon && selection.areaPolygon.length > 0 && hasActiveGroup
  );

  const pointQuery = trpc.landContext.resolveBoundaryAtPoint.useQuery(
    { lon: selection?.point?.[0] ?? 0, lat: selection?.point?.[1] ?? 0 },
    { enabled: pointEnabled }
  );

  const areaBbox = useMemo(() => {
    if (!selection?.areaPolygon || selection.areaPolygon.length === 0) return null;
    const lons = selection.areaPolygon.map((p) => p[0]);
    const lats = selection.areaPolygon.map((p) => p[1]);
    return {
      west: Math.min(...lons),
      south: Math.min(...lats),
      east: Math.max(...lons),
      north: Math.max(...lats),
    };
  }, [selection]);

  const areaQuery = trpc.landContext.resolveBoundaryInArea.useQuery(
    { bbox: areaBbox ?? { west: -125, south: 42, east: -111, north: 49 } },
    { enabled: areaEnabled }
  );

  const active = selection?.mode === "area" ? areaQuery : pointQuery;

  return useMemo(() => {
    if (!selection || !hasActiveGroup) {
      return { data: [], meta: null, isLoading: false, isError: false, error: null };
    }

    if (active.data && "status" in active.data && active.data.status === "budget_exceeded") {
      return {
        data: [],
        meta: {
          totalCount: 0,
          returnedCount: 0,
          hasMore: false,
          budgetExceeded: {
            reason: active.data.reason,
            limit: active.data.limit,
            requested: active.data.requested,
          },
        },
        isLoading: false,
        isError: false,
        error: null,
      };
    }

    const results: LandContextResult[] =
      active.data && "status" in active.data && active.data.status === "ok" ? active.data.data : [];

    const features = toResults(results, enabledGroups);

    return {
      data: features,
      meta: {
        totalCount: results.length,
        returnedCount: features.length,
        hasMore: false,
        partialCoverage: results.some((r) => r.coverageState === "partial_area_coverage"),
      },
      isLoading: active.isLoading,
      isError: active.isError,
      error: active.error as Error | null,
    };
  }, [active.data, active.isLoading, active.isError, active.error, selection, hasActiveGroup, enabledGroups]);
}
