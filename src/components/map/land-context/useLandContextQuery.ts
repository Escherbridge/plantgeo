import { useMemo } from "react";
import { trpc } from "@/lib/trpc/client";
import {
  LAND_CONTEXT_GROUP_IDS,
  type LandContextFeature,
  type LandContextGroupId,
  type LandContextQueryStatus,
  type LandContextResultMeta,
  type LandContextSelectionInput,
} from "@/stores/land-context-store";
import type { LandContextResult } from "@/lib/environmental/land-context-contract";
import { getRegion } from "@/lib/region/region";

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
 * fabricate a feature -- see `toFeature` below -- but they are not dropped
 * either: their typed state and verbatim gap strings travel out through
 * `meta.coverageNotices`, which is what lets the UI say "no source admitted
 * for reads yet" in the reader's own words instead of showing a blank map.
 */
export interface UseLandContextQueryResult {
  data: LandContextFeature[];
  meta: LandContextResultMeta | null;
  status: LandContextQueryStatus;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
}

/**
 * What the boundary procedures actually return: the frozen contract result
 * plus the boundary geometry the router decoded server-side
 * (`attachDecodedGeometry` in `src/lib/server/services/land-context/geometry/`).
 * Declared structurally here rather than imported, because this file is
 * browser code and may not import from `@/lib/server/**`
 * (`scripts/check-client-server-imports.mjs`); assigning the inferred tRPC
 * output to this type in `useLandContextQuery` is what keeps the two in step
 * at compile time.
 */
type BoundaryResult = LandContextResult & { geometry: GeoJSON.Geometry | null };

/**
 * The store's `geometry` is non-nullable, so a source that carried no
 * geometry is represented by an EMPTY GeometryCollection: MapLibre draws
 * nothing for it, the accessible list and the panel still list it, and no
 * shape the source did not provide is ever fabricated. A decoded geometry
 * from the router replaces this whenever `geometryWkb` was present.
 */
const NO_GEOMETRY: GeoJSON.GeometryCollection = { type: "GeometryCollection", geometries: [] };

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

function toFeature(result: BoundaryResult, index: number): LandContextFeature | null {
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
    geometry: result.geometry ?? NO_GEOMETRY,
    contactVerified: result.route?.status === "active",
  };
}

function toResults(
  results: BoundaryResult[],
  enabledGroups: Record<LandContextGroupId, boolean>
): LandContextFeature[] {
  return results
    .map((result, index) => toFeature(result, index))
    .filter((feature): feature is LandContextFeature => feature !== null && enabledGroups[feature.group]);
}

/** Every non-matched result, kept as a typed coverage statement with its verbatim gap strings. */
function toCoverageNotices(
  results: BoundaryResult[]
): NonNullable<LandContextResultMeta["coverageNotices"]> {
  return results
    .filter((result) => result.coverageState !== "matched")
    .map((result) => ({ coverageState: result.coverageState, gaps: result.unresolvedGaps }));
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
    { bbox: areaBbox ?? getRegion().defaultCameraEnvelope },
    { enabled: areaEnabled }
  );

  const active = selection?.mode === "area" ? areaQuery : pointQuery;

  return useMemo(() => {
    if (!selection || !hasActiveGroup) {
      return { data: [], meta: null, status: "idle", isLoading: false, isError: false, error: null };
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
          coverageNotices: [],
        },
        status: "settled",
        isLoading: false,
        isError: false,
        error: null,
      };
    }

    const results: BoundaryResult[] =
      active.data && "status" in active.data && active.data.status === "ok" ? active.data.data : [];

    const features = toResults(results, enabledGroups);
    const matchedCount = results.filter((result) => result.coverageState === "matched").length;

    // A response that arrived is settled even when it holds only coverage statements; anything
    // short of a response is still in flight unless the transport already failed.
    const status: LandContextQueryStatus = active.isError
      ? "error"
      : active.data
        ? "settled"
        : "loading";

    return {
      data: features,
      meta: {
        totalCount: matchedCount,
        returnedCount: features.length,
        hasMore: false,
        partialCoverage: results.some((r) => r.coverageState === "partial_area_coverage"),
        coverageNotices: toCoverageNotices(results),
      },
      status,
      isLoading: active.isLoading,
      isError: active.isError,
      error: active.error as Error | null,
    };
  }, [active.data, active.isLoading, active.isError, active.error, selection, hasActiveGroup, enabledGroups]);
}
