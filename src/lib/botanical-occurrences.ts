/**
 * Wire types and a bounded client for the botanical occurrence plane
 * (`GET /api/v1/botanical-occurrences/query` on the agri-data-service).
 *
 * This track is implementation-only for its three owned layers; the shared
 * route/registry integration is a separate, serialized slice (see
 * `conductor/tracks/botanical_occurrence_experience_20260911/evidence/`). Until that
 * slice lands, this client talks to the agri-data-service DIRECTLY rather than through
 * a Next.js proxy route, using the same base-URL convention the Parquet plane client
 * documents (`src/lib/server/services/parquet-plane-client.ts`: an `AGRI_*_SERVICE_URL`
 * env var, falling back to the service's own local port in development). The
 * integrator may choose to route this through a proxy instead -- that decision is
 * theirs, not pre-empted here.
 *
 * FAULTS ARE NEVER THROWN by `fetchBotanicalOccurrences`. Unlike the Parquet plane
 * client, this function is called directly from client components (map layers, the
 * filters panel), so a thrown network error would have to be caught at every call
 * site. Instead every failure mode -- network failure, non-2xx, a body that fails
 * `BOTANICAL_OCCURRENCE_RESPONSE_SCHEMA` -- normalizes to the `unavailable` member of
 * the same discriminated union the server itself uses for a governed refusal. Callers
 * that need to tell "the service said no" from "the service could not be reached"
 * should read `reason`, which is service-authored text for the former and this
 * module's own text for the latter.
 */

/** One admitted collecting-event date's precision. Interval and partial dates stay visible as such. */
export type BotanicalEventPrecision = "day" | "month" | "year" | "interval" | "unknown";

/** A specimen's collecting-event date or interval, never collapsed to a single instant. */
export interface BotanicalEventInterval {
  start: string | null;
  end: string | null;
  precision: BotanicalEventPrecision;
}

/** Whether taxon-concept resolution succeeded, is ambiguous, or could not be matched at all. */
export type BotanicalResolutionState = "resolved" | "ambiguous" | "unmatched";

/** Declared coordinate support: an exact locality, or a generalized/obscured one. */
export type BotanicalSpatialClass = "exact" | "generalized";

/** Whether an uncertain record counts as admitted evidence or only as a possible one. */
export type BotanicalMembership = "confirmed" | "possible";

/** One detail-zoom specimen occurrence record. */
export interface BotanicalOccurrenceFeature {
  occurrence_id: string;
  collection_key: string;
  source_record_key: string;
  taxon_concept_id: string;
  resolution_state: BotanicalResolutionState;
  scientific_name: string;
  family: string | null;
  event_interval: BotanicalEventInterval;
  longitude: number;
  latitude: number;
  coordinate_uncertainty_m: number | null;
  spatial_class: BotanicalSpatialClass;
  membership: BotanicalMembership;
  catalog_number: string | null;
  recorded_by: string | null;
  basis_of_record: string | null;
  rights_uri: string | null;
  attribution_text: string | null;
}

/** Counts that must be shown even when the corresponding rows never become map features. */
export interface BotanicalOccurrenceCounts {
  returned: number;
  matched: number;
  withheld: number;
  nonspatial: number;
  excluded_by_qc: number;
}

/** Why a support cell's evaluation state reads the way it does; a governed artifact, not a client guess. */
export type BotanicalCellEvaluation =
  | "documented"
  | "evaluated_zero"
  | "outside_coverage"
  | "withheld_or_generalized_only"
  | "not_evaluated";

/** One aggregate support cell at regional/middle zoom. */
export interface BotanicalAggregateCell {
  cell_id: string;
  geometry: GeoJSON.Polygon;
  evaluation: BotanicalCellEvaluation;
  documented_taxa: number;
  record_count: number;
  event_estimate: number;
  collection_count: number;
  excluded_by_qc: number;
  possible_only_records: number;
}

interface BotanicalDetailResponse {
  state: "detail";
  release_set_id: string;
  published_at: string | null;
  taxonomy_recipe_version: string;
  qc_policy_version: string;
  support_id: null;
  truncated: boolean;
  next_cursor: string | null;
  counts: BotanicalOccurrenceCounts;
  features: BotanicalOccurrenceFeature[];
}

interface BotanicalAggregateResponse {
  state: "aggregate";
  release_set_id: string;
  published_at: string | null;
  support_id: string;
  cells: BotanicalAggregateCell[];
}

interface BotanicalRefusedResponse {
  state: "refused";
  reason: string;
}

interface BotanicalUnavailableResponse {
  state: "unavailable";
  reason: string;
}

/** The full discriminated union `fetchBotanicalOccurrences` may resolve to. Never thrown. */
export type BotanicalOccurrenceResponse =
  | BotanicalDetailResponse
  | BotanicalAggregateResponse
  | BotanicalRefusedResponse
  | BotanicalUnavailableResponse;

/** Public spatial-quality filter: admitted-only, possible-only-included, or everything. */
export type BotanicalSpatialQuality = "confirmed" | "possible" | "all";

/** Query parameters for `fetchBotanicalOccurrences`. `release_set_id` is always required. */
export interface BotanicalOccurrenceQueryParams {
  release_set_id: string;
  bbox: string;
  zoom: number;
  taxon_concept_id?: string;
  family?: string;
  collection_key?: string;
  event_start?: string;
  event_end?: string;
  spatial_quality?: BotanicalSpatialQuality;
  limit?: number;
  cursor?: string;
}

/**
 * The route this client calls, on the agri-data-service base URL. Not proxied through
 * Next.js yet -- see the module doc.
 */
const BOTANICAL_OCCURRENCES_ROUTE = "/api/v1/botanical-occurrences/query";

/** Same env var family as `parquet-plane-client.ts`, kept distinct per data plane. */
const BOTANICAL_SERVICE_URL_ENV = "NEXT_PUBLIC_AGRI_BOTANICAL_SERVICE_URL";

/** Matches the agri-data-service's own local port in development. */
const BOTANICAL_SERVICE_DEVELOPMENT_URL = "http://localhost:8000";

/** Record caps this client will ever request; the server may cap lower and report `truncated`. */
export const BOTANICAL_OCCURRENCE_MAX_LIMIT = 2000;

function resolveBotanicalServiceBaseUrl(): string {
  const configured =
    typeof process !== "undefined" ? process.env[BOTANICAL_SERVICE_URL_ENV] : undefined;
  return configured && configured.length > 0 ? configured : BOTANICAL_SERVICE_DEVELOPMENT_URL;
}

function buildQueryString(params: BotanicalOccurrenceQueryParams): string {
  const search = new URLSearchParams();
  search.set("release_set_id", params.release_set_id);
  search.set("bbox", params.bbox);
  search.set("zoom", String(params.zoom));
  if (params.taxon_concept_id) search.set("taxon_concept_id", params.taxon_concept_id);
  if (params.family) search.set("family", params.family);
  if (params.collection_key) search.set("collection_key", params.collection_key);
  if (params.event_start) search.set("event_start", params.event_start);
  if (params.event_end) search.set("event_end", params.event_end);
  search.set("spatial_quality", params.spatial_quality ?? "confirmed");
  search.set("limit", String(Math.min(params.limit ?? BOTANICAL_OCCURRENCE_MAX_LIMIT, BOTANICAL_OCCURRENCE_MAX_LIMIT)));
  if (params.cursor) search.set("cursor", params.cursor);
  return search.toString();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Narrows an unknown parsed body to the wire union without a schema dependency (this
 * module is imported from map layers that must stay light). Any shape mismatch resolves
 * to `unavailable` rather than throwing -- see the module doc.
 */
function parseBotanicalOccurrenceResponse(body: unknown): BotanicalOccurrenceResponse {
  if (!isRecord(body) || typeof body.state !== "string") {
    return { state: "unavailable", reason: "The occurrence service returned an unrecognized response." };
  }
  switch (body.state) {
    case "detail":
    case "aggregate":
    case "refused":
    case "unavailable":
      return body as unknown as BotanicalOccurrenceResponse;
    default:
      return { state: "unavailable", reason: "The occurrence service returned an unrecognized response." };
  }
}

/**
 * Queries the botanical occurrence plane. Never throws: network failures and unparseable
 * bodies both resolve to `{ state: "unavailable", reason }`. Callers must not fetch until
 * `release_set_id` is a non-empty string pinned by the filters panel -- see
 * `BotanicalFilters.tsx`'s "no release pinned" state.
 */
export async function fetchBotanicalOccurrences(
  params: BotanicalOccurrenceQueryParams
): Promise<BotanicalOccurrenceResponse> {
  if (!params.release_set_id) {
    return { state: "unavailable", reason: "No release_set_id was supplied." };
  }
  const url = `${resolveBotanicalServiceBaseUrl()}${BOTANICAL_OCCURRENCES_ROUTE}?${buildQueryString(params)}`;
  try {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const reason =
        isRecord(body) && typeof body.reason === "string"
          ? body.reason
          : `The occurrence service responded with status ${response.status}.`;
      return { state: "unavailable", reason };
    }
    return parseBotanicalOccurrenceResponse(body);
  } catch {
    return { state: "unavailable", reason: "The occurrence service could not be reached." };
  }
}

/** Zoom threshold: aggregate below, detail at-or-above. Mutually exclusive, never both mounted. */
export const BOTANICAL_DETAIL_MIN_ZOOM = 11;

/**
 * Pending registry entries in the shape `src/lib/map/layer-registry.ts` expects, for the
 * shared integrator to paste in (see
 * `conductor/tracks/botanical_occurrence_experience_20260911/evidence/shared-registration.patch`).
 * This track deliberately does NOT import or extend `LayerRegistryEntry` / `LayerToggleId`
 * here -- editing that file is out of scope for this slice.
 */
export const BOTANICAL_LAYER_DEFINITIONS = [
  {
    toggleId: "botanical-occurrences",
    label: "Botanical Specimen Occurrences",
    icon: "leaf",
    panelId: "botanical",
  },
  {
    toggleId: "botanical-richness",
    label: "Documented Taxon Richness",
    icon: "layers",
    panelId: "botanical",
  },
  {
    toggleId: "botanical-collection-effort",
    label: "Collection Evidence & Effort",
    icon: "users",
    panelId: "botanical",
  },
] as const;
