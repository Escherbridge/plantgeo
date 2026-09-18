/**
 * Parquet-plane storage layer for the land-context reference plane.
 *
 * Two published products, two phases, one pointer GET and one data GET per read -- the shape
 * `conductor/code_styleguides/layer-lanes.md` §4a requires. See
 * `src/lib/server/services/land-context/AGENTS.md` for why the two products are denormalized,
 * why a parcel-key read still stops short, and what an empty answer here does and does not prove.
 */

import { getParquetLatestRelease, getParquetWarehouseCoverage } from "@/lib/server/services/parquet-plane-client";
import type { ParquetLaneCoverage } from "@/lib/server/services/parquet-plane-client";
import { ZOOM_TIERS, zoomTierPathSegment, type ZoomTier } from "@/lib/map/zoom-tiers";
import { type RungSelectionResult, selectFinestAdmittingRungResult } from "@/lib/map/rung-selection";
import { parquetUpstreamFailure } from "@/lib/server/services/parquet-trpc-readers/shared";
import { assertExhaustiveParquetPlaneState } from "@/lib/server/services/parquet-envelope";
import { z } from "zod";
import { MAX_FEATURES_RETURNED, PILOT_STATES } from "./budgets";
import type {
  BoundaryVersionRef,
  CoverageState,
  OrganizationOfficeRef,
  OverlapBasis,
  ParcelKey,
  PlaceOfficeTopicRelationshipRef,
  PublicContactRouteRef,
  RouteMeaning,
  SourceReleaseRef,
} from "./types";
import { decodeBoundaryGeometry } from "./geometry/boundary-geometry-adapter";

// Re-exported so callers of this reader can decode
// `CandidateBoundaryFeature.boundary.geometryWkb` without importing from `./geometry` directly.
// See `./geometry/boundary-geometry-adapter.ts` for the null-vs-throw contract: a `null`
// `geometryWkb` decodes to `null`, malformed-but-present WKB throws `WkbDecodeError`.
export { decodeBoundaryGeometry };

/**
 * The two Parquet products this reader reads, by their layer slug.
 *
 * NEITHER SLUG IS REGISTERED IN `pipeline/parquet/lane_registry.py` TODAY. They are a declared
 * expectation, and the warehouse coverage census -- not this constant -- is what decides whether
 * anything is published: an unregistered slug simply never appears in the census, and every read
 * below reports that as a stated gap rather than as an empty collection.
 */
export const LAND_CONTEXT_PRODUCT_LAYERS = {
  /** `land_context.boundary_versions` joined to its `source_releases` row, one flat row per feature. */
  boundaries: "land-context-boundaries",
  /** `place_office_topic_relationships` joined to `organizations` and `public_contact_routes`. */
  contacts: "land-context-contacts",
} as const;

/**
 * Largest bbox, in square degrees, each rung of the ladder will answer for.
 *
 * Mirrors the shape of `MAX_BBOX_SQUARE_DEGREES` in `planes/botanical_occurrences.py` and exists
 * for the reason the 2026-09-14 handoff recorded against that plane: a rung whose budget is too
 * tight for a viewport that legitimately wants regional coverage refuses the read at exactly the
 * zooms a reader cares about. Selecting the rung from zoom AND bbox size, rather than zoom alone,
 * is what keeps a wide viewport on a coarse rung instead of refusing it on a fine one.
 */
export const RUNG_MAX_BBOX_SQUARE_DEGREES: Readonly<Record<ZoomTier, number>> = {
  13: 4,
  9: 100,
  5: 1_600,
  0: 64_800,
};

/** Half-width of the bbox a point read probes with; a degenerate bbox is not a readable rectangle. */
const POINT_PROBE_PAD_DEGREES = 0.0001;

/** Only `observed` partitions: this plane publishes no forecast stream and never will. */
const LAND_CONTEXT_PARTITION_KIND = "observed";

export interface BboxDegrees {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface CandidateBoundaryFeature {
  boundary: BoundaryVersionRef;
  overlapBasis: OverlapBasis;
  sourceRelease: SourceReleaseRef;
}

/* -------------------------------------------------------------------------
 * Pointer phase: the warehouse coverage census
 * ---------------------------------------------------------------------- */

/** One resolved partition to read, as `pruneCandidatesByBbox` hands it to `exactIntersectCandidates`. */
interface ServingPartition {
  layer: string;
  zoomTier: ZoomTier;
  day: string;
}

/**
 * `layer/kind=observed/zoom=NN/day=YYYY-MM-DD`, the partition prefix the data GET will touch.
 *
 * The candidate keys this reader hands out are real partition identities rather than opaque
 * tokens, so the second phase reads exactly what the first phase proved published WITHOUT a
 * second census: one pointer GET, then one data GET, per §4a.
 */
const PARTITION_KEY_PATTERN =
  /^(?<layer>[a-z0-9-]+)\/kind=observed\/zoom=(?<zoom>\d{2})\/day=(?<day>\d{4}-\d{2}-\d{2})$/;

function partitionKey(partition: ServingPartition): string {
  // `zoomTierPathSegment` already renders the whole `zoom=NN` segment, padding included.
  return `${partition.layer}/kind=${LAND_CONTEXT_PARTITION_KIND}/${zoomTierPathSegment(partition.zoomTier)}/day=${partition.day}`;
}

function parsePartitionKey(key: string): ServingPartition | null {
  const matched = PARTITION_KEY_PATTERN.exec(key);
  if (matched?.groups === undefined) return null;
  const zoom = Number(matched.groups.zoom);
  const zoomTier = ZOOM_TIERS.find((tier) => tier === zoom);
  if (zoomTier === undefined) return null;
  return { layer: matched.groups.layer, zoomTier, day: matched.groups.day };
}

export function bboxSquareDegrees(bbox: BboxDegrees): number {
  return Math.max(0, bbox.east - bbox.west) * Math.max(0, bbox.north - bbox.south);
}

/**
 * The finest published rung whose own ceiling admits this bbox, as a discriminated result (S8, W3
 * review). This call site never sets `finestAllowed`, so `rung_not_on_ladder` is structurally
 * unreachable here -- callers still switch on `kind` because the type is shared with the botanical
 * plane's call, which DOES set it, and a caller that only checked `=== null` is exactly how the
 * two refusal kinds collapsed into one sentence in the first place.
 *
 * Finest-that-fits, walking the ladder from z13 down: a caller that asks for a small area gets
 * the most detailed rung published for it, and a caller that asks for a regional one is moved
 * DOWN the ladder rather than refused -- the third of the three fix directions the 2026-09-14
 * handoff left undecided for the botanical plane, and since the owner decision of 2026-09-18 the
 * rule on that plane too. The walk itself lives in `@/lib/map/rung-selection` so the two lanes
 * cannot drift into two selection rules; only the ladder and its ceilings are local.
 */
export function selectServingRung(
  publishedTiers: readonly ZoomTier[],
  bboxAreaSquareDegrees: number
): RungSelectionResult<ZoomTier> {
  return selectFinestAdmittingRungResult({
    coarsestFirst: ZOOM_TIERS,
    maxBboxSquareDegrees: RUNG_MAX_BBOX_SQUARE_DEGREES,
    areaSquareDegrees: bboxAreaSquareDegrees,
    isPublished: (tier) => publishedTiers.includes(tier),
  });
}

/** A read that produced no partition to touch, with the census's own words for why. */
/**
 * A refusal to name a partition, as a TYPED coverage finding plus the sentence explaining it.
 *
 * The state is the field machine consumers branch on; `detail` is prose for a caption and for
 * `unresolvedGaps`. Before 2026-09-18 only the prose existed, so `reader.ts` resolved every refusal
 * -- including "no lane is registered anywhere" and "the census request timed out" -- to
 * `partial_area_coverage`, a positive coverage assertion (STYLE-REVIEW-W2 B3). Callers that cannot
 * yet carry a state still read `detail` through the `gap` channel.
 */
export interface CoverageRefusal {
  coverageState: CoverageState;
  detail: string;
}

interface PointerRefusal extends CoverageRefusal {
  partition: null;
}

interface PointerResolution {
  partition: ServingPartition;
  detail: "";
}

function refusal(coverageState: CoverageState, detail: string): PointerRefusal {
  return { partition: null, coverageState, detail };
}

/** Every census lane describing one product's observed stream, whatever its rung. */
function lanesForProduct(
  lanes: readonly ParquetLaneCoverage[],
  layer: string
): ParquetLaneCoverage[] {
  return lanes.filter((lane) => lane.layer === layer && lane.kind === LAND_CONTEXT_PARTITION_KIND);
}

/**
 * Pointer GET: which partition, if any, can answer this product for this bbox.
 *
 * Never throws an upstream fault at the caller. A transport failure is reported as a gap, because
 * this module's callers publish its outcome as a COVERAGE state and "the request did not complete"
 * must never be rendered as "the warehouse published nothing" -- the same rule
 * `parquet-envelope.ts` states for the four warehouse states.
 */
async function resolveServingPartition(
  layer: string,
  bboxAreaSquareDegrees: number
): Promise<PointerResolution | PointerRefusal> {
  let census: Awaited<ReturnType<typeof getParquetWarehouseCoverage>>;
  try {
    census = await getParquetWarehouseCoverage();
  } catch (error) {
    const failure = parquetUpstreamFailure(error);
    if (failure === null) throw error;
    return refusal(
      "upstream_unavailable",
      `land-context coverage census unavailable (${failure.fault.kind}): ${failure.fault.message}; this is a transport failure, not a coverage finding`
    );
  }

  const productLanes = lanesForProduct(census.lanes, layer);
  if (productLanes.length === 0) {
    return refusal(
      "source_unbound_for_region",
      `no Parquet lane named "${layer}" appears in the warehouse coverage census; the land-context reference plane binds no source for it in this region`
    );
  }

  const withheld = productLanes.filter((lane) => lane.withheldReason !== null);
  const readable = productLanes.filter(
    (lane) => lane.withheldReason === null && lane.latestDay !== null
  );
  if (readable.length === 0) {
    const reasons = [...new Set(withheld.map((lane) => lane.withheldReason))].join(", ");
    return refusal(
      "unknown_coverage",
      reasons.length > 0
        ? `every published rung of "${layer}" is withheld by its availability index (${reasons})`
        : `"${layer}" is registered but has written no day on any rung`
    );
  }

  const publishedTiers = readable.map((lane) => lane.zoomTier);
  const rungSelection = selectServingRung(publishedTiers, bboxAreaSquareDegrees);
  if (rungSelection.kind === "rung_not_on_ladder") {
    // Structurally unreachable from this call site (no `finestAllowed` is ever set above), but
    // named so a future caller that DOES set one is not silently folded into the area refusal.
    return refusal(
      "unknown_coverage",
      `rung z${rungSelection.rung} was selected but is not on the published ladder for "${layer}"`
    );
  }
  if (rungSelection.kind === "no_rung_admits_area") {
    const coarsest = Math.min(...publishedTiers) as ZoomTier;
    return refusal(
      "unknown_coverage",
      `a ${bboxAreaSquareDegrees.toFixed(2)} square degree request exceeds every published rung of "${layer}"; the coarsest published rung (z${coarsest}) is bounded at ${RUNG_MAX_BBOX_SQUARE_DEGREES[coarsest]} square degrees`
    );
  }
  const zoomTier = rungSelection.rung;

  const lane = readable.find((candidate) => candidate.zoomTier === zoomTier);
  // `selectServingRung` chose from `publishedTiers`, so the lane it named is always present.
  if (lane === undefined || lane.latestDay === null) {
    return refusal(
      "unknown_coverage",
      `rung z${zoomTier} of "${layer}" vanished between census read and partition select`
    );
  }

  return {
    partition: { layer, zoomTier, day: lane.latestDay },
    detail: "",
  };
}

/* -------------------------------------------------------------------------
 * Data phase: the row schemas each product must publish
 * ---------------------------------------------------------------------- */

const pilotStateSchema = z.enum(PILOT_STATES);
const nullableText = z.string().nullable();

/**
 * One flat boundary row: `land_context.boundary_versions` joined to its `source_releases` row.
 *
 * Column names mirror the relational declarations in
 * `src/lib/server/db/schema/land-context/` (the only schema this plane declares anywhere) --
 * `family`, not `family_type`; `geom_wkb` names the encoding because Parquet has no PostGIS type
 * and the frozen contract carries hex WKB. Deliberately NOT `.strict()`, unlike the readers in
 * `parquet-trpc-readers/`: those mirror registered lanes whose columns are frozen, and this one
 * states a MINIMUM a lane that does not exist yet must publish.
 */
const boundaryRowSchema = z.object({
  source_namespace: z.string().min(1),
  native_feature_key: z.string().min(1),
  native_feature_version: nullableText,
  family: z.string().min(1),
  interest_type: z.string().min(1),
  state: pilotStateSchema,
  county: nullableText,
  geom_wkb: nullableText,
  release_publisher: z.string().min(1),
  release_canonical_endpoint: z.string().min(1),
  release_source_version: z.string().min(1),
  release_captured_at: nullableText,
  release_source_watermark_at: nullableText,
  release_admission_verdict: z.enum(["admitted", "rejected", "pending"]),
});

/** The five route meanings, in `RouteMeaning`'s own order; `toRouteRef`'s assignment ties them. */
const routeMeaningSchema = z.enum([
  "records_assistance",
  "responsible_agency_program",
  "advisory_sme",
  "contact_process_inquiry",
  "documented_introduction_forwarding",
]);

/** Compile-time tie: a divergence between the schema and the contract fails here, not at runtime. */
export const ROUTE_MEANING_VALUES: readonly RouteMeaning[] = routeMeaningSchema.options;

/** One flat contact row: a relationship joined to the office it names and that office's route. */
const contactRowSchema = z.object({
  subject_id: z.string().min(1),
  object_id: z.string().min(1),
  relationship_kind: z.string().min(1),
  applicable_geography: nullableText,
  documented_topic: nullableText,
  assignment_method: z.string().min(1),
  review_status: z.enum(["reviewed", "unreviewed", "unknown"]),
  effective_from: nullableText,
  effective_to: nullableText,
  source_evidence_url: nullableText,
  organization_id: z.string().min(1),
  office_id: z.string().min(1),
  official_public_name: z.string().min(1),
  office_type: z.string().min(1),
  parent_organization_id: nullableText,
  route_type: z.string().min(1),
  route_meaning: routeMeaningSchema,
  documented_help: nullableText,
  official_inquiry_url: nullableText,
  public_business_phone: nullableText,
  public_business_email: nullableText,
  published_professional_name: nullableText,
  route_status: z.enum(["active", "stale", "broken", "unverified"]),
  verified_at: nullableText,
  forwarding_documented: z.boolean(),
});

type BoundaryRow = z.infer<typeof boundaryRowSchema>;
type ContactRow = z.infer<typeof contactRowSchema>;

function toBoundaryRef(row: BoundaryRow): BoundaryVersionRef {
  return {
    sourceNamespace: row.source_namespace,
    nativeFeatureKey: row.native_feature_key,
    nativeFeatureVersion: row.native_feature_version,
    familyType: row.family,
    interestType: row.interest_type,
    state: row.state,
    county: row.county,
    geometryWkb: row.geom_wkb,
  };
}

function toSourceReleaseRef(row: BoundaryRow): SourceReleaseRef {
  return {
    publisher: row.release_publisher,
    canonicalEndpoint: row.release_canonical_endpoint,
    sourceVersion: row.release_source_version,
    captureTime: row.release_captured_at,
    sourceEffectiveTime: row.release_source_watermark_at,
    // `source_releases` declares no publication-date column; null rather than restating the
    // watermark, which is a change clock and not a release date.
    sourcePublishedTime: null,
    admissionVerdict: row.release_admission_verdict,
  };
}

/**
 * How a row returned by a bbox-filtered Parquet read overlaps the caller's selection.
 *
 * Always `bbox_intersection`, never `point_containment` or `exact_geometry_intersection`, even on
 * the point read: the plane filters by rectangle, so a returned feature is a CANDIDATE whose
 * containment nobody has proved. Claiming otherwise would be the reduction the reference-plane
 * spec forbids -- "Intersection finds candidate reported features, not legal proof."
 */
function bboxOverlapBasis(partition: ServingPartition, bbox: BboxDegrees): OverlapBasis {
  return {
    kind: "bbox_intersection",
    description: `reported feature whose bounding rectangle intersects ${bbox.west},${bbox.south},${bbox.east},${bbox.north} on rung z${partition.zoomTier} of ${partition.layer} (release day ${partition.day}); containment is not proved`,
  };
}

/** Rows this product published for the resolved partition, or the stated reason there are none. */
interface ProductRows<TRow> {
  rows: TRow[];
  gap: string;
}

async function readProductRows<TRow>(
  partition: ServingPartition,
  rowSchema: z.ZodType<TRow>,
  bbox: BboxDegrees | null
): Promise<ProductRows<TRow>> {
  let envelope: Awaited<ReturnType<typeof getParquetLatestRelease>>;
  try {
    envelope = await getParquetLatestRelease({
      layer: partition.layer,
      asOfDay: partition.day,
      zoomTier: partition.zoomTier,
      kind: LAND_CONTEXT_PARTITION_KIND,
      ...(bbox === null
        ? {}
        : { bbox: `${bbox.west},${bbox.south},${bbox.east},${bbox.north}` }),
    });
  } catch (error) {
    const failure = parquetUpstreamFailure(error);
    if (failure === null) throw error;
    return {
      rows: [],
      gap: `land-context read of ${partition.layer} failed (${failure.fault.kind}): ${failure.fault.message}; this is a transport failure, not a coverage finding`,
    };
  }

  switch (envelope.state) {
    case "published": {
      const parsed = z.array(rowSchema).safeParse(envelope.rows);
      if (!parsed.success) {
        return {
          rows: [],
          gap: `${partition.layer} rows do not match the schema this reader declares; see src/lib/server/services/land-context/AGENTS.md`,
        };
      }
      return {
        rows: parsed.data,
        gap: envelope.truncated
          ? `${partition.layer} hit the serving row budget on ${envelope.servedDay}; the answer is a subset, not the whole partition`
          : "",
      };
    }
    case "governed_absence":
      return {
        rows: [],
        gap: `${partition.layer} recorded a governed absence for ${envelope.servedDay}: ${envelope.evidence.reason} (upstream said "${envelope.evidence.upstreamResponse}", run ${envelope.evidence.runId})`,
      };
    case "day_not_written":
      return {
        rows: [],
        gap: `${partition.layer} has written nothing for ${envelope.requestedDay} on rung z${partition.zoomTier}`,
      };
    case "lane_never_written":
      return {
        rows: [],
        gap: `${partition.layer} has never written a partition on any day`,
      };
    default:
      return assertExhaustiveParquetPlaneState(envelope);
  }
}

/* -------------------------------------------------------------------------
 * The reads
 * ---------------------------------------------------------------------- */

/**
 * Step 1 of the two-phase spatial read: the pointer GET.
 *
 * The candidate key it returns names the exact published partition step 2 will read, so the pair
 * costs one pointer GET and one data GET. Returns no candidates -- with the census's own words --
 * whenever the lane is unregistered, withheld, or too coarse for this bbox. AN EMPTY CANDIDATE
 * SET IS NEVER A COVERAGE FINDING; read the gap.
 */
export async function pruneCandidatesByBbox(
  bbox: BboxDegrees
): Promise<{ candidateKeys: string[]; gap: string; refusal: CoverageRefusal | null }> {
  const resolved = await resolveServingPartition(
    LAND_CONTEXT_PRODUCT_LAYERS.boundaries,
    bboxSquareDegrees(bbox)
  );
  if (resolved.partition === null) {
    return {
      candidateKeys: [],
      gap: resolved.detail,
      refusal: { coverageState: resolved.coverageState, detail: resolved.detail },
    };
  }
  return { candidateKeys: [partitionKey(resolved.partition)], gap: "", refusal: null };
}

/**
 * Step 2: the data GET, against the partitions step 1 proved published.
 *
 * Never runs without candidates, so there is no path here that scans the warehouse unbounded.
 * The intersection is the plane's own bbox filter; see `bboxOverlapBasis` for why that is
 * reported as a candidate overlap and not as containment.
 */
export async function exactIntersectCandidates(
  candidateKeys: string[],
  bbox: BboxDegrees
): Promise<{ features: CandidateBoundaryFeature[]; gap: string }> {
  if (candidateKeys.length === 0) {
    return {
      features: [],
      gap: "no candidate partition was pruned for this bbox; nothing was read",
    };
  }

  const features: CandidateBoundaryFeature[] = [];
  const gaps: string[] = [];
  for (const key of candidateKeys) {
    const partition = parsePartitionKey(key);
    if (partition === null) {
      gaps.push(`candidate key "${key}" is not a partition key this reader issued`);
      continue;
    }
    const { rows, gap } = await readProductRows(partition, boundaryRowSchema, bbox);
    if (gap.length > 0) gaps.push(gap);
    for (const row of rows) {
      features.push({
        boundary: toBoundaryRef(row),
        overlapBasis: bboxOverlapBasis(partition, bbox),
        sourceRelease: toSourceReleaseRef(row),
      });
    }
  }
  return { features, gap: gaps.join("; ") };
}

/**
 * Point read, structured the same two phases: the point is probed as a small square, because a
 * degenerate rectangle is not a readable bbox. Every returned feature is a CANDIDATE -- the plane
 * filtered by rectangle and nothing here has run a polygon-contains test.
 */
export async function findContainingFeatures(
  lon: number,
  lat: number
): Promise<{ features: CandidateBoundaryFeature[]; gap: string; refusal: CoverageRefusal | null }> {
  const probe: BboxDegrees = {
    west: lon - POINT_PROBE_PAD_DEGREES,
    south: lat - POINT_PROBE_PAD_DEGREES,
    east: lon + POINT_PROBE_PAD_DEGREES,
    north: lat + POINT_PROBE_PAD_DEGREES,
  };
  const pruned = await pruneCandidatesByBbox(probe);
  if (pruned.candidateKeys.length === 0) {
    return { features: [], gap: pruned.gap, refusal: pruned.refusal };
  }
  return { ...(await exactIntersectCandidates(pruned.candidateKeys, probe)), refusal: null };
}

/**
 * Parcel-key lookup. STOPS SHORT, on purpose, and says so.
 *
 * The frozen Parquet wire (`parquet-plane-client.ts` §WIRE) offers day, window, release and
 * coverage reads and no key-addressed one, so resolving a parcel key means either a key-index
 * product this plane does not publish or a full scan of a boundary lane -- and an unbounded scan
 * is exactly what the reference-plane spec forbids. The pointer GET still runs, so the gap
 * distinguishes "no lane at all" from "a lane that is simply not key-addressable".
 */
export async function findBoundaryByParcelKey(
  key: ParcelKey
): Promise<{ feature: CandidateBoundaryFeature | null; gap: string }> {
  const resolved = await resolveServingPartition(
    LAND_CONTEXT_PRODUCT_LAYERS.boundaries,
    RUNG_MAX_BBOX_SQUARE_DEGREES[13]
  );
  if (resolved.partition === null) return { feature: null, gap: resolved.detail };
  return {
    feature: null,
    gap: `${resolved.partition.layer} publishes rung z${resolved.partition.zoomTier}, but the frozen Parquet wire exposes no key-addressed read; ${key.sourceNamespace}:${key.originalId} cannot be resolved without a parcel-key index product or a bounding box`,
  };
}

/**
 * Relationship/contact lookup against the denormalized contacts product.
 *
 * Read WITHOUT a bbox and filtered in memory: a relationship/office/route join is a small
 * reference table with no geometry to prune on, and the serving side's own row budget bounds it
 * (a hit is reported through `truncated`, which becomes a gap here rather than a silent subset).
 * That is the one read in this module that is not spatially pruned, and it is bounded by the
 * product's nature rather than by a rectangle.
 */
export async function findRelationshipsAndRoutes(
  subjectId: string,
  topic: string | null
): Promise<{
  relationships: PlaceOfficeTopicRelationshipRef[];
  offices: OrganizationOfficeRef[];
  routes: PublicContactRouteRef[];
  gap: string;
}> {
  const empty = { relationships: [], offices: [], routes: [] };
  const resolved = await resolveServingPartition(
    LAND_CONTEXT_PRODUCT_LAYERS.contacts,
    RUNG_MAX_BBOX_SQUARE_DEGREES[0]
  );
  if (resolved.partition === null) return { ...empty, gap: resolved.detail };

  const { rows, gap } = await readProductRows(resolved.partition, contactRowSchema, null);
  const matching = rows
    .filter((row) => row.subject_id === subjectId)
    .filter((row) => topic === null || row.documented_topic === topic);
  if (matching.length === 0) return { ...empty, gap };
  if (matching.length > MAX_FEATURES_RETURNED) {
    return {
      ...empty,
      gap: `${resolved.partition.layer} holds ${matching.length} relationships for subject ${subjectId}, over the ${MAX_FEATURES_RETURNED} feature budget; refused rather than truncated`,
    };
  }

  return {
    relationships: matching.map(toRelationshipRef),
    offices: dedupeByOfficeId(matching.map(toOfficeRef)),
    routes: matching.map(toRouteRef),
    gap,
  };
}

function toRelationshipRef(row: ContactRow): PlaceOfficeTopicRelationshipRef {
  return {
    subjectId: row.subject_id,
    objectId: row.object_id,
    relationshipKind: row.relationship_kind,
    applicableGeography: row.applicable_geography,
    documentedTopic: row.documented_topic,
    assignmentMethod: row.assignment_method,
    reviewStatus: row.review_status,
    effectiveFrom: row.effective_from,
    effectiveTo: row.effective_to,
    sourceEvidenceUrl: row.source_evidence_url,
  };
}

function toOfficeRef(row: ContactRow): OrganizationOfficeRef {
  return {
    organizationId: row.organization_id,
    officeId: row.office_id,
    officialPublicName: row.official_public_name,
    officeType: row.office_type,
    parentOrganizationId: row.parent_organization_id,
  };
}

function toRouteRef(row: ContactRow): PublicContactRouteRef {
  return {
    officeId: row.office_id,
    routeType: row.route_type,
    routeMeaning: row.route_meaning,
    documentedTopic: row.documented_topic,
    documentedHelp: row.documented_help,
    officialInquiryUrl: row.official_inquiry_url,
    publicPhone: row.public_business_phone,
    publicEmail: row.public_business_email,
    optionalProfessionalName: row.published_professional_name,
    status: row.route_status,
    verificationTime: row.verified_at,
    supportsIntroductionOrForwarding: row.forwarding_documented,
  };
}

/** One office per ID: the denormalized product repeats an office once per route it owns. */
function dedupeByOfficeId(offices: readonly OrganizationOfficeRef[]): OrganizationOfficeRef[] {
  return [...new Map(offices.map((office) => [office.officeId, office])).values()];
}

/**
 * Coverage status for a state/county.
 *
 * Always `null` -- unknown -- and the two gaps say which unknown it is. The warehouse census
 * reports what a LANE published, never which counties a source covered, so answering `false`
 * here would claim a proven-coverage area that nothing has proved; `reader.ts` would then render
 * it as `no_match_in_proven_coverage`. Closing this needs a per-region coverage product, not a
 * cleverer read of the census.
 */
export async function readCoverageStatus(
  state: string,
  county: string | null
): Promise<{ covered: boolean | null; gap: string }> {
  const place = county === null ? state : `${county}, ${state}`;
  const resolved = await resolveServingPartition(
    LAND_CONTEXT_PRODUCT_LAYERS.boundaries,
    RUNG_MAX_BBOX_SQUARE_DEGREES[0]
  );
  if (resolved.partition === null) return { covered: null, gap: resolved.detail };
  return {
    covered: null,
    gap: `${resolved.partition.layer} publishes rung z${resolved.partition.zoomTier} as of ${resolved.partition.day}, but no per-region coverage product states whether ${place} is within admitted coverage`,
  };
}
