import { z } from "zod";
import { fetchBoundedJson, providerUrl } from "@/lib/server/http/bounded-upstream";

/**
 * Bounded client for the agri-data-service `botanical-occurrences` plane: the herbarium specimen
 * read (UBC v16.43 and successors) mounted 2026-09-13.
 *
 * DELIBERATE SIBLING, NOT AN EXTENSION of `parquet-plane-client.ts`. That file's `WIRE` block is
 * frozen and dual-tested against `services/agri-data-service/tests/contract/wire_contract.py` --
 * one Python fixture pair proves four specific routes (day/window/release/coverage) byte-for-byte.
 * `botanical-occurrences` is a separate plane with its own two routes, its own response shapes, and
 * no existing contract-test pairing (confirmed: no `*botanical*` file under
 * `services/agri-data-service/tests/contract/`). Bolting it onto the frozen file would either widen
 * what that freeze claims to cover without a matching Python fixture, or force an unrelated route
 * change to wait on a contract test that says nothing about it. This module borrows the SAME
 * architecture on purpose -- bounded fetch, thrown transport/contract faults, zod-validated wire
 * decoding, an explicit WIRE block as the one place route/param/field names live -- without joining
 * the frozen pairing. If this plane earns its own contract fixture later, that is a separate change.
 *
 * FAULTS ARE THROWN FOR TRANSPORT/CONTRACT BREAKS, RETURNED FOR PLANE STATES. A timeout, oversized
 * body, 5xx, unreachable host or a 200 whose body breaks the published shape throws (mirrors
 * `parquet-plane-client.ts`'s "FAULTS ARE THROWN, NEVER ENVELOPED" for those same failure modes).
 * The plane's own declared states -- `detail` / `aggregate` / `refused` / `unavailable` -- are
 * returned as a discriminated union rather than thrown, matching how `ParquetPlaneEnvelope`'s
 * `governed_absence` / `day_not_written` states are consumed downstream: a caller switches on
 * `.state` rather than catching four different exception types for four answers that are not
 * failures of this client, just facts (or refusals) the plane is reporting honestly.
 */

/** Base URL of the agri-data-service host -- the SAME service and env var `parquet-plane-client.ts` uses. */
const SERVICE_URL_ENV = "AGRI_PARQUET_SERVICE_URL";

/** Matches the agri-data-service's own local port. */
const SERVICE_DEVELOPMENT_URL = "http://localhost:8000";

/**
 * Byte ceiling for a `/query` read.
 *
 * Sized off `MAX_LIMIT = 2000` rows (`botanical_occurrences.py:55`) of `_feature()` objects
 * (`botanical_occurrences.py:333-357`), each carrying a handful of short strings, two floats and a
 * nested interval -- roughly 500 bytes worst case, so 2000 rows is ~1 MiB of feature payload. 8 MiB
 * leaves generous headroom for aggregate cell polygons (which carry a 5-point ring each) without
 * approaching the sibling client's 16 MiB row-read ceiling, since this plane's rows are far smaller
 * than an arbitrary layer's `z.record(z.unknown())` row.
 */
const MAX_QUERY_RESPONSE_BYTES = 8 * 1024 * 1024;

/** `/current` answers one small pointer object; no reason to share the query ceiling. */
const MAX_CURRENT_RESPONSE_BYTES = 64 * 1024;

/** A DuckDB/Polars scan over one generation's parquet parts, with headroom for a cold read. */
const QUERY_TIMEOUT_MS = 15_000;

/** Resolving a small JSON pointer should never wait as long as a row scan. */
const CURRENT_TIMEOUT_MS = 5_000;

/**
 * How long one `/current` answer is reused, in seconds.
 *
 * Unlike `parquet-plane-client.ts`'s day/window/release reads (always `no-store`, since a live edge
 * is still being written), this pointer is exactly the kind of thing worth a short cache: it moves
 * only on a new publication, which is infrequent, and every caller in a short window should agree on
 * which release_set_id to pin rather than each resolving a slightly different "current" underneath a
 * single page load. 120 seconds is picked to sit well inside the 60-300s band this task calls out --
 * short enough that a republish is visible within two minutes, long enough to collapse a burst of
 * concurrent `getBotanicalOccurrences` calls (each of which internally resolves the pointer) into one
 * upstream `/current` read via Next.js's data cache `revalidate` window, mirroring
 * `COVERAGE_REVALIDATE_SECONDS`'s reasoning in the sibling client without copying its stricter
 * single-flight/memoization machinery -- this pointer is cheap enough that a bare `revalidate`
 * window is proportionate; see the judgment-call note in the module's return value below.
 */
const CURRENT_REVALIDATE_SECONDS = 120;

/** A caller handed this client something it will not put on the wire. Never a transport fault. */
export class BotanicalOccurrencesRequestError extends Error {}

/**
 * The service answered 200 and the body broke the published contract. Permanent until a deploy
 * fixes one side -- mirrors `ParquetPlaneContractError`'s reasoning for why this is its own class
 * rather than treated as a retryable transport fault.
 */
export class BotanicalOccurrencesContractError extends Error {}

/**
 * The `/current` pointer resolved to `unavailable` -- no generation has ever been published, or the
 * pointer itself is unreadable. Thrown rather than returned as a union member: every caller of
 * `getCurrentBotanicalReleaseSetId` wants a `release_set_id` string or an exception, not a second
 * discriminated union to unwrap before it can even ask `/query` a question.
 */
export class BotanicalOccurrencesUnavailableError extends Error {
  constructor(public readonly reason: string) {
    super(`botanical-occurrences current pointer is unavailable: ${reason}`);
  }
}

/* ---------------------------------------------------------------------------
 * WIRE
 *
 * The ONLY place this client states what the HTTP contract looks like, mirroring the sibling
 * client's convention. Route segments, query parameter names and response field names live here.
 *
 * Sourced from `services/agri-data-service/src/agri_data_service/planes/botanical_occurrences.py`
 * and `.../interface/http/botanical_occurrences.py`:
 *  1. Routes hang off `/api/v1/botanical-occurrences/` (blueprint prefix + app.py mount).
 *  2. `/current` answers 200 `state: "current"` with `release_set_id` and an OPTIONAL
 *     `published_at` (omitted entirely when the manifest carries none, never sent as null --
 *     `read_current_botanical_release` only sets the key when `published_at is not None`), or 503
 *     `state: "unavailable"` with `reason` and `note`.
 *  3. `/query` requires `release_set_id` (an exact pinned generation id; the literal `"current"` is
 *     refused server-side) and `bbox` as `"west,south,east,north"` (`_parse_bbox`,
 *     `botanical_occurrences.py:136-146` -- despite the task prompt's "presumably", the source
 *     confirms `minLon,minLat,maxLon,maxLat` order). Optional: `zoom` (plain integer; `>=
 *     DETAIL_ZOOM_FLOOR` (11) answers `detail`, below it answers `aggregate` -- this plane's own
 *     floor, NOT on `ZOOM_TIERS`'s 0/5/9/13 ladder, so this client takes a raw integer zoom rather
 *     than a `ZoomTier`), `taxon_concept_id`, `family`, `collection_key`, `event_start`/`event_end`
 *     (`YYYY-MM-DD`), `spatial_quality` (`confirmed` | `possible` | `all`), `limit`, `cursor`.
 *  4. `/query` answers exactly one of FOUR states, all HTTP 200 or the refusal/unavailable status
 *     the adapter maps (`_status_for`, `botanical_occurrences.py`(http):44-50): `detail`
 *     (`_read_detail`, lines 360-407), `aggregate` (`_read_aggregate`, lines 410-464), `refused`
 *     (`refused()`, lines 225-233 -- 400 for a caller mistake, 409 for a publication-state problem),
 *     `unavailable` (`unavailable()`, lines 236-243 -- 503).
 *  5. `published_at`, `taxonomy_recipe_version`, `qc_policy_version` on `detail`/`aggregate` are all
 *     `manifest.get(...)`, i.e. may be `null` on the wire (never omitted, unlike `/current`'s
 *     `published_at`) -- decoded as nullable, not optional.
 * ------------------------------------------------------------------------- */

const WIRE = {
  basePath: "/api/v1/botanical-occurrences",
  routes: {
    current: "current",
    query: "query",
  },
  params: {
    releaseSetId: "release_set_id",
    bbox: "bbox",
    zoom: "zoom",
    taxonConceptId: "taxon_concept_id",
    family: "family",
    collectionKey: "collection_key",
    eventStart: "event_start",
    eventEnd: "event_end",
    spatialQuality: "spatial_quality",
    limit: "limit",
    cursor: "cursor",
  },
} as const;

/** `/current`'s two wire shapes: `published_at` OMITTED (never null) when the manifest has none. */
const wireCurrentSchema = z.discriminatedUnion("state", [
  z.object({
    product: z.literal("botanical-occurrences"),
    state: z.literal("current"),
    release_set_id: z.string(),
    published_at: z.string().optional(),
  }),
  z.object({
    product: z.literal("botanical-occurrences"),
    state: z.literal("unavailable"),
    reason: z.string(),
    note: z.string(),
  }),
]);

/** `_feature()`, `botanical_occurrences.py:333-357`. */
const wireFeatureSchema = z.object({
  occurrence_id: z.string(),
  collection_key: z.string(),
  source_record_key: z.string(),
  taxon_concept_id: z.string(),
  resolution_state: z.string(),
  scientific_name: z.string().nullable(),
  family: z.string().nullable(),
  event_interval: z.object({
    start: z.string().nullable(),
    end: z.string().nullable(),
    precision: z.string().nullable(),
  }),
  longitude: z.number(),
  latitude: z.number(),
  coordinate_uncertainty_m: z.number().nullable(),
  spatial_class: z.string(),
  membership: z.enum(["confirmed", "possible"]).nullable(),
  catalog_number: z.string().nullable(),
  recorded_by: z.string().nullable(),
  basis_of_record: z.string().nullable(),
  rights_uri: z.string().nullable(),
  attribution_text: z.string().nullable(),
});

const wireDetailCountsSchema = z.object({
  returned: z.number().int(),
  matched: z.number().int(),
  withheld: z.number().int(),
  nonspatial: z.number().int(),
  excluded_by_qc: z.number().int(),
});

/** The aggregate `cells` shape, `_read_aggregate`, roughly `botanical_occurrences.py:425-448`. */
const wireCellSchema = z.object({
  cell_id: z.string(),
  geometry: z.object({
    type: z.literal("Polygon"),
    coordinates: z.array(z.array(z.tuple([z.number(), z.number()]))),
  }),
  evaluation: z.string(),
  documented_taxa: z.number().int(),
  record_count: z.number().int(),
  event_estimate: z.number().int().nullable(),
  collection_count: z.number().int(),
  excluded_by_qc: z.number().int(),
  possible_only_records: z.number().int(),
});

const wireAggregateCountsSchema = z.object({
  returned: z.number().int(),
  matched: z.number().int(),
});

const wireQuerySchema = z.discriminatedUnion("state", [
  z.object({
    product: z.literal("botanical-occurrences"),
    state: z.literal("detail"),
    release_set_id: z.string(),
    published_at: z.string().nullable(),
    taxonomy_recipe_version: z.string().nullable(),
    qc_policy_version: z.string().nullable(),
    support_id: z.null(),
    features: z.array(wireFeatureSchema),
    truncated: z.boolean(),
    next_cursor: z.string().nullable(),
    counts: wireDetailCountsSchema,
  }),
  z.object({
    product: z.literal("botanical-occurrences"),
    state: z.literal("aggregate"),
    release_set_id: z.string(),
    published_at: z.string().nullable(),
    taxonomy_recipe_version: z.string().nullable(),
    qc_policy_version: z.string().nullable(),
    support_id: z.string(),
    cells: z.array(wireCellSchema),
    truncated: z.boolean(),
    next_cursor: z.string().nullable(),
    counts: wireAggregateCountsSchema,
  }),
  z.object({
    product: z.literal("botanical-occurrences"),
    state: z.literal("refused"),
    reason: z.string(),
    detail: z.string(),
    note: z.string(),
  }),
  z.object({
    product: z.literal("botanical-occurrences"),
    state: z.literal("unavailable"),
    reason: z.string(),
    note: z.string(),
  }),
]);

/* ------------------------------------------------------------------------- */

/** This codebase's vocabulary for one `/query` feature; decoded from `wireFeatureSchema`. */
export interface BotanicalOccurrenceFeature {
  occurrenceId: string;
  collectionKey: string;
  sourceRecordKey: string;
  taxonConceptId: string;
  resolutionState: string;
  scientificName: string | null;
  family: string | null;
  eventInterval: { start: string | null; end: string | null; precision: string | null };
  longitude: number;
  latitude: number;
  coordinateUncertaintyMeters: number | null;
  spatialClass: string;
  membership: "confirmed" | "possible" | null;
  catalogNumber: string | null;
  recordedBy: string | null;
  basisOfRecord: string | null;
  rightsUri: string | null;
  attributionText: string | null;
}

/** This codebase's vocabulary for one `/query` aggregate cell; decoded from `wireCellSchema`. */
export interface BotanicalOccurrenceCell {
  cellId: string;
  geometry: GeoJSON.Polygon;
  evaluation: string;
  documentedTaxa: number;
  recordCount: number;
  eventEstimate: number | null;
  collectionCount: number;
  excludedByQc: number;
  possibleOnlyRecords: number;
}

/** Fields shared by the `detail` and `aggregate` answers. */
interface BotanicalOccurrenceQueryBase {
  releaseSetId: string;
  publishedAt: string | null;
  taxonomyRecipeVersion: string | null;
  qcPolicyVersion: string | null;
  truncated: boolean;
  nextCursor: string | null;
}

/** The plane's own four `/query` states, as a discriminated union a caller switches on. */
export type BotanicalOccurrencesQueryResult =
  | (BotanicalOccurrenceQueryBase & {
      state: "detail";
      features: BotanicalOccurrenceFeature[];
      counts: { returned: number; matched: number; withheld: number; nonspatial: number; excludedByQc: number };
    })
  | (BotanicalOccurrenceQueryBase & {
      state: "aggregate";
      supportId: string;
      cells: BotanicalOccurrenceCell[];
      counts: { returned: number; matched: number };
    })
  | { state: "refused"; reason: string; detail: string; note: string }
  | { state: "unavailable"; reason: string; note: string };

/** The caller's own filter params for one bounded `/query` question. */
export interface GetBotanicalOccurrencesRequest {
  /** `"west,south,east,north"`, matching `_parse_bbox`'s `minLon,minLat,maxLon,maxLat` order. */
  bbox: string;
  /**
   * The map's raw zoom, NOT a `ZoomTier` off `zoom-tiers.ts`. This plane's detail floor (11) sits
   * between that ladder's z9 and z13 rungs and derives its aggregate support id from continuous
   * zoom bands (`FINE_SUPPORT_ZOOM_FLOOR = 7`) that do not align with the four-tier ladder at all --
   * see the WIRE block above. A caller passes its own integer zoom (e.g. `Math.floor(map.getZoom())`
   * or the unrounded value; the service does its own `int()` coercion server-side).
   */
  zoom: number;
  taxonConceptId?: string;
  family?: string;
  collectionKey?: string;
  /** `YYYY-MM-DD`. */
  eventStart?: string;
  /** `YYYY-MM-DD`. */
  eventEnd?: string;
  spatialQuality?: "confirmed" | "possible" | "all";
  limit?: number;
  cursor?: string;
  /** The caller's cancellation, combined with this read's own timeout. */
  signal?: AbortSignal;
}

/** The service root, with one route appended. Never reads `process.env` directly. */
function endpoint(route: string): URL {
  const url = providerUrl(SERVICE_URL_ENV, SERVICE_DEVELOPMENT_URL);
  url.pathname = `${url.pathname.replace(/\/$/, "")}${WIRE.basePath}/${route}`;
  return url;
}

function decodeFeature(wire: z.infer<typeof wireFeatureSchema>): BotanicalOccurrenceFeature {
  return {
    occurrenceId: wire.occurrence_id,
    collectionKey: wire.collection_key,
    sourceRecordKey: wire.source_record_key,
    taxonConceptId: wire.taxon_concept_id,
    resolutionState: wire.resolution_state,
    scientificName: wire.scientific_name,
    family: wire.family,
    eventInterval: {
      start: wire.event_interval.start,
      end: wire.event_interval.end,
      precision: wire.event_interval.precision,
    },
    longitude: wire.longitude,
    latitude: wire.latitude,
    coordinateUncertaintyMeters: wire.coordinate_uncertainty_m,
    spatialClass: wire.spatial_class,
    membership: wire.membership,
    catalogNumber: wire.catalog_number,
    recordedBy: wire.recorded_by,
    basisOfRecord: wire.basis_of_record,
    rightsUri: wire.rights_uri,
    attributionText: wire.attribution_text,
  };
}

function decodeCell(wire: z.infer<typeof wireCellSchema>): BotanicalOccurrenceCell {
  return {
    cellId: wire.cell_id,
    geometry: wire.geometry,
    evaluation: wire.evaluation,
    documentedTaxa: wire.documented_taxa,
    recordCount: wire.record_count,
    eventEstimate: wire.event_estimate,
    collectionCount: wire.collection_count,
    excludedByQc: wire.excluded_by_qc,
    possibleOnlyRecords: wire.possible_only_records,
  };
}

function decodeQuery(payload: unknown): BotanicalOccurrencesQueryResult {
  const parsed = wireQuerySchema.safeParse(payload);
  if (!parsed.success) {
    throw new BotanicalOccurrencesContractError(
      "botanical-occurrences /query answered a body that is not one of the four published states"
    );
  }
  const wire = parsed.data;
  switch (wire.state) {
    case "detail":
      return {
        state: "detail",
        releaseSetId: wire.release_set_id,
        publishedAt: wire.published_at,
        taxonomyRecipeVersion: wire.taxonomy_recipe_version,
        qcPolicyVersion: wire.qc_policy_version,
        truncated: wire.truncated,
        nextCursor: wire.next_cursor,
        features: wire.features.map(decodeFeature),
        counts: {
          returned: wire.counts.returned,
          matched: wire.counts.matched,
          withheld: wire.counts.withheld,
          nonspatial: wire.counts.nonspatial,
          excludedByQc: wire.counts.excluded_by_qc,
        },
      };
    case "aggregate":
      return {
        state: "aggregate",
        releaseSetId: wire.release_set_id,
        publishedAt: wire.published_at,
        taxonomyRecipeVersion: wire.taxonomy_recipe_version,
        qcPolicyVersion: wire.qc_policy_version,
        truncated: wire.truncated,
        nextCursor: wire.next_cursor,
        supportId: wire.support_id,
        cells: wire.cells.map(decodeCell),
        counts: { returned: wire.counts.returned, matched: wire.counts.matched },
      };
    case "refused":
      return { state: "refused", reason: wire.reason, detail: wire.detail, note: wire.note };
    case "unavailable":
      return { state: "unavailable", reason: wire.reason, note: wire.note };
  }
}

/**
 * Resolve the pointer to the release_set_id a caller should pin, via `GET /current`.
 *
 * Cached for `CURRENT_REVALIDATE_SECONDS` through Next.js's `fetch` data cache (`revalidate`),
 * mirroring the freshness reasoning `COVERAGE_REVALIDATE_SECONDS` documents in the sibling client
 * without that function's explicit in-process single-flight/memo map -- this pointer's request is
 * cheap (a `/current.json` read behind the object store) and infrequent per unique caller, so a bare
 * cache-header window was judged proportionate. If concurrent-request storms against a cold cache
 * ever show up in practice, promoting this to the same single-flighted `Promise` pattern as
 * `getParquetWarehouseCoverage` is a contained follow-up, not a redesign.
 *
 * Throws `BotanicalOccurrencesUnavailableError` when no generation has ever been published, or the
 * pointer is unreadable. Throws `BotanicalOccurrencesContractError` on a malformed 200.
 */
export async function getCurrentBotanicalReleaseSetId(signal?: AbortSignal): Promise<string> {
  const url = endpoint(WIRE.routes.current);
  const payload = await fetchBoundedJson(
    url,
    { method: "GET", headers: { Accept: "application/json" } },
    {
      maxBytes: MAX_CURRENT_RESPONSE_BYTES,
      timeoutMs: CURRENT_TIMEOUT_MS,
      revalidateSeconds: CURRENT_REVALIDATE_SECONDS,
      ...(signal === undefined ? {} : { signal }),
    }
  );
  const parsed = wireCurrentSchema.safeParse(payload);
  if (!parsed.success) {
    throw new BotanicalOccurrencesContractError(
      "botanical-occurrences /current answered a body that is not one of its two published states"
    );
  }
  if (parsed.data.state === "unavailable") {
    throw new BotanicalOccurrencesUnavailableError(parsed.data.reason);
  }
  return parsed.data.release_set_id;
}

/**
 * One bounded, release-pinned occurrence query. Resolves `release_set_id` internally via
 * `getCurrentBotanicalReleaseSetId` -- the caller never names a generation, closing the gap the
 * service's own refusal of the literal `"current"` creates for a plane that has no other pointer.
 *
 * Returns the plane's four states as a discriminated union (see `BotanicalOccurrencesQueryResult`)
 * rather than throwing for `refused`/`unavailable`; see the module docstring for why. A transport
 * fault, an oversized/absent body, or a 200 that breaks the contract still throws.
 */
export async function getBotanicalOccurrences(
  request: GetBotanicalOccurrencesRequest
): Promise<BotanicalOccurrencesQueryResult> {
  if (request.bbox.trim() === "") {
    throw new BotanicalOccurrencesRequestError("bbox must be a non-empty \"west,south,east,north\" string");
  }
  const releaseSetId = await getCurrentBotanicalReleaseSetId(request.signal);

  const url = endpoint(WIRE.routes.query);
  url.searchParams.set(WIRE.params.releaseSetId, releaseSetId);
  url.searchParams.set(WIRE.params.bbox, request.bbox);
  url.searchParams.set(WIRE.params.zoom, String(Math.trunc(request.zoom)));
  if (request.taxonConceptId !== undefined) {
    url.searchParams.set(WIRE.params.taxonConceptId, request.taxonConceptId);
  }
  if (request.family !== undefined) url.searchParams.set(WIRE.params.family, request.family);
  if (request.collectionKey !== undefined) {
    url.searchParams.set(WIRE.params.collectionKey, request.collectionKey);
  }
  if (request.eventStart !== undefined) url.searchParams.set(WIRE.params.eventStart, request.eventStart);
  if (request.eventEnd !== undefined) url.searchParams.set(WIRE.params.eventEnd, request.eventEnd);
  if (request.spatialQuality !== undefined) {
    url.searchParams.set(WIRE.params.spatialQuality, request.spatialQuality);
  }
  if (request.limit !== undefined) url.searchParams.set(WIRE.params.limit, String(request.limit));
  if (request.cursor !== undefined) url.searchParams.set(WIRE.params.cursor, request.cursor);

  const payload = await fetchBoundedJson(
    url,
    { method: "GET", headers: { Accept: "application/json" } },
    {
      maxBytes: MAX_QUERY_RESPONSE_BYTES,
      timeoutMs: QUERY_TIMEOUT_MS,
      ...(request.signal === undefined ? {} : { signal: request.signal }),
    }
  );
  return decodeQuery(payload);
}
