import { and, desc, eq, gte, lte, sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { features, layers } from "@/lib/server/db/schema";
import {
  getSoilProperties,
  type SoilProperties,
} from "@/lib/server/services/soilgrids";
import {
  getMTBSPerimeters,
  type MTBSFireProperties,
} from "@/lib/server/services/mtbs";
import {
  getStrategyRecommendations,
  type StrategyScore,
} from "@/lib/server/services/strategy-scoring";
import {
  getPublishedDroughtClassification,
  getPublishedFireDetections,
  getPublishedStreamflowGauges,
  getPublishedWeatherForBbox,
  getPublishedWeatherForPoint,
  getSliderCapabilities,
  resolveRequestedObservationDay,
  serverCurrentDate,
  type PublishedWeatherObservation,
  type ResolvedSliderCapabilities,
  type ResolvedSliderLayerCapability,
} from "@/lib/server/services/environmental-read-model";
import {
  getInterventionSuitability,
  type InterventionSuitability,
} from "@/lib/server/services/carbon-potential";
import type { WaterGauge } from "@/lib/server/services/usgs-water";
import { droughtLevelAtPoint } from "@/lib/server/services/alert-engine";
import {
  REGIONAL_EVIDENCE_SOURCES,
  type RegionalEvidenceSource,
} from "@/lib/regional-intelligence";
import {
  isLayerToggleId,
  toggleIdForWarehouseLayerName,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import { DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS } from "@/lib/map/tile-layer-date-filter";
import { hasSelectableDay, isDayDescribed } from "@/stores/time-slider-store";
import { SLIDER_STREAM_LAYER_NAMES } from "@/types/time-slider";

/** Half-width of the context window around the requested point, in degrees. */
const CONTEXT_RADIUS_DEGREES = 0.25;
const NEAREST_GAUGE_MAX_DEGREES = 0.5;
const MAX_FIRE_DETECTIONS = 25;
const MAX_FIRE_PERIMETERS = 10;
const MAX_MTBS_FIRES = 10;

export interface NearbyFireDetection {
  observedAt: string;
  lat: number;
  lon: number;
  confidence: string | null;
  frp: number | null;
}

export interface NearbyFirePerimeter {
  name: string;
  irwinId: string | null;
  updatedAt: string;
}

/**
 * One signed-in contributor's intervention recommendation still awaiting expert review, read
 * for its proximity to the agent's queried point. Mirrors the shape
 * `interventionsRouter.listProposed` (`src/lib/server/trpc/routers/interventions.ts`) already
 * serves to the feed UI; this is a second, independent read of the same rows rather than a call
 * through tRPC, because a server-side context assembler has no request/session to route through.
 */
export interface CommunityProposal {
  id: string;
  name: string | null;
  type: string | null;
  description: string | null;
  distanceMeters: number;
  createdAt: string;
}

/**
 * What produced a `StrategyContextEntry`, so the prompt and the UI can say so rather than let a
 * ranking read as a validated prediction.
 *
 * `"heuristic_score"` — `strategy-scoring.ts`'s rule-based `StrategyScore` list (currently always
 * empty: that plane has no validated evidence release published yet, see
 * `getStrategyRecommendationResult`).
 * `"evaluation_only_model"` — a `geo.mv_strategy_recommendations_*` row (drizzle 0027, rebuilt
 * over real geometry by 0028). No row on that plane has ever been signed off by an owner: every
 * receipt feeding it is CHECK-pinned `evaluation_only` with `CHECK (NOT publication_authorized)`,
 * and each served cell carries its own `label_review_tier` saying what its label release is
 * worth — down to `no_label_release_bound` when nothing can be cited at all. Never causal: see
 * the governance note on `resolveStrategyContext` below.
 */
export type StrategyClaimTier = "heuristic_score" | "evaluation_only_model";

/**
 * A strategy candidate for this point, carrying only what is safe to hand an LLM regardless of
 * which tier produced it: a name and a relative ranking. Never a causal effect size, an
 * expected-benefit percentage, or anything else that could be read as a validated outcome.
 */
export interface StrategyContextEntry {
  claimTier: StrategyClaimTier;
  strategySlug: string;
  name: string;
  category: string | null;
  /** A relative suitability ranking from its own source, not an effect size. */
  score: number;
}

export interface RegionalContextPayload {
  location: { lat: number; lon: number; geohash: string };
  strategyRecommendations: StrategyScore[] | null;
  /** Top strategy candidates for this point. See `resolveStrategyContext`. */
  strategyContext: StrategyContextEntry[];
  /** Nearby unreviewed community intervention proposals. See `readCommunityProposals`. */
  communityProposals: CommunityProposal[];
  soilProperties: SoilProperties | null;
  waterScarcity: {
    droughtClass: string | null;
    nearestGauge: WaterGauge | null;
  } | null;
  weather: PublishedWeatherObservation | null;
  fireDetections: { detections: NearbyFireDetection[]; totalCount: number } | null;
  firePerimeters: { perimeters: NearbyFirePerimeter[]; totalCount: number } | null;
  mtbsPerimeters: { fires: GeoJSON.Feature[]; totalCount: number } | null;
  carbonPotential: InterventionSuitability | null;
}

/* ---------------------------------------------------------------------------
 * The days the user is actually looking at
 *
 * Until 2026-08-09 the map carried ONE global day and this assembler took none: every read
 * answered for the live edge, which was correct because the live edge was the only thing on
 * screen. Per-layer sliders ended that. A user scrubbed to August 2025 who asks "what is
 * happening here" must not be answered about today, and the rows on screen are no longer even
 * on the same day as each other.
 * ------------------------------------------------------------------------- */

/** One layer row's day, exactly as the client reported it. */
export interface ViewedLayerRequest {
  /** The row's `LayerToggleId`, or the `geo.layers.name` behind it; both are accepted. */
  layer: string;
  /** YYYY-MM-DD the row is scrubbed to. */
  date: string;
  /** The client's own capability check for that day. Cross-checked here, never trusted alone. */
  hasDataOnDate: boolean;
}

/** What a read of one payload block, at one viewed day, actually did. */
export type ViewedDateReadOutcome =
  /** The read ran for that day and returned observations at this location. */
  | "observed_on_viewed_date"
  /**
   * The layer published on that day and none of it falls in this location's window. A real,
   * observed absence -- the one outcome that MAY be reported as "none here".
   */
  | "published_with_nothing_at_this_location"
  /**
   * The layer published nothing at all on that day. An ingestion hole, never a measurement.
   * `fire-detections` currently carries a 980-day one, so this is a live outcome and not a
   * defensive branch: an agent told "0 detections" for such a day states an absence that was
   * never observed.
   */
  | "not_published_on_viewed_date"
  /** Nothing published here AND no coverage record can say whether the day was ingested. */
  | "coverage_unknown_on_viewed_date"
  /** The day itself is not observable: in the future, or not a calendar date. */
  | "viewed_date_not_observable"
  /** The reader accepts no day, so the value served is the live edge rather than the viewed day. */
  | "served_as_of_latest"
  /**
   * The read did not complete. Kept distinct for the same reason `MetricAtDateAvailability`
   * carries "request_failed": a fault must never be reported as the warehouse publishing nothing.
   */
  | "read_failed"
  /** The row is on the user's screen but feeds no block of this payload. */
  | "not_represented_in_payload";

/**
 * Whether the payload block a viewed row feeds holds the SET the map is drawing for that row.
 *
 * A second axis entirely from `ViewedDateReadOutcome`, and it has to be: the outcome says what
 * the server's read did, and every branch of it can be true of a block that describes a
 * different set of features than the one on the user's screen. `firePerimeters` is the live
 * case. It is excluded from `DATE_PARAMETERISED_SOURCES` because WFIGS publishes no per-feature
 * observation time, so it is read at the live edge -- while `fire-perimeters` is an `event`
 * layer that DOES get a slider and IS in `DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS`, so the map
 * draws only perimeters observed on or before the viewed day. A user scrubbed to 2024-06-01
 * sees three perimeters and asks what is burning; without this the agent answers about today's
 * set and attributes it to its own observation time, describing perimeters that are not on
 * screen and saying nothing about the three that are. The date vocabulary alone cannot prevent
 * that: nothing it says is false, and the correspondence the model assumes was never stated.
 */
export type ViewedLayerSetCorrespondence =
  /** The block was read at this row's own viewed day, so it describes the day the row draws. */
  | "payload_is_the_viewed_day"
  /** The map bounds this row by the viewed day; the block is the live edge. Different sets. */
  | "map_bounded_by_viewed_day_payload_is_latest"
  /** The block is the live edge and this row's draw is bounded by no day at all. */
  | "map_unbounded_payload_is_latest"
  /** The row feeds no block, so there is no set to compare it against. */
  | "no_payload_block_for_this_row";

/** One viewed row, resolved against what the server's own read of that day did. */
export interface ViewedLayerReading {
  layer: string;
  viewedDate: string;
  /** Carried through verbatim so the prompt can state the client's claim as the client's. */
  clientReportsDataOnDate: boolean;
  evidenceSource: RegionalEvidenceSource | null;
  outcome: ViewedDateReadOutcome;
  /** Why the outcome is not a plain observation; null only when it is one. */
  reason: string | null;
  /** True when the client's own claim contradicts the layer's server-side coverage record. */
  clientClaimContradicted: boolean;
  /** Whether what the agent was handed is the set the map is drawing for this row. */
  setCorrespondence: ViewedLayerSetCorrespondence;
}

/** Everything the agent needs to know about WHEN the payload it is holding describes. */
export interface TemporalContext {
  /** The server's own today, so every viewed day is anchored to a clock the agent can see. */
  serverCurrentDate: string;
  /** True when the client named no rows: an older client, or nothing visible. */
  viewedLayersUnreported: boolean;
  readings: ViewedLayerReading[];
  /** Distinct viewed days, ascending. More than one means the map is a mixed-time composite. */
  viewedDates: string[];
  /** Blocks no viewed row named. Served at the live edge, exactly as they always were. */
  sourcesServedAsOfLatest: RegionalEvidenceSource[];
}

export interface RegionalContextResult {
  payload: RegionalContextPayload;
  dataFreshness: Record<string, string>;
  /** True when no warehouse layer resolved; the agent must say so rather than infer. */
  contextIsEmpty: boolean;
  /** Which day each block describes, and which blocks could not be read for a day at all. */
  temporalContext: TemporalContext;
  cacheHit: boolean;
}

/**
 * Which payload block a layer row drives, keyed by `LayerToggleId` AND by `geo.layers.name`.
 *
 * Both keyings are accepted deliberately. The row lives in the client's layer registry while
 * the block is named for the warehouse read, and the two halves of this feature ship
 * independently. A client sending the other name must degrade to a reported "viewed but
 * unmapped" row, never to a silently dropped date that leaves the agent answering about today.
 *
 * `drought` is aliased like the rest since 2026-08-09: it still has no `geo.layers` row -- it
 * lives in geo.drought_areas as weekly releases -- but it is published as a slider STREAM under
 * `SLIDER_STREAM_LAYER_NAMES.drought`, so that is the name a client may send for it.
 */
const EVIDENCE_SOURCE_BY_VIEWED_LAYER: Record<string, RegionalEvidenceSource> = {
  fire: "fireDetections",
  "fire-detections": "fireDetections",
  "fire-perimeters": "firePerimeters",
  water: "streamflow",
  "water-gauges": "streamflow",
  drought: "drought",
  [SLIDER_STREAM_LAYER_NAMES.drought]: "drought",
  weather: "weatherObservations",
  "weather-observations": "weatherObservations",
};

/**
 * The blocks whose reader accepts a named day. Everything else is served at the live edge and
 * must SAY so rather than let its value be attributed to the day the user is looking at.
 *
 * `firePerimeters` is absent on purpose and not by oversight: WFIGS publishes no per-feature
 * observation time, so row `updatedAt` is the only time signal the perimeter read has (see
 * `readPublishedFirePerimeters`) and there is no honest way to ask it for a past day.
 * `strategyRecommendations` and `carbonPotential` are derived scores over the live warehouse.
 * `soilProperties` (SoilGrids, via `getSoilProperties`) and `mtbsPerimeters` (via
 * `getMTBSPerimeters`) are populated from live external reads as of 2026-08-14, but neither
 * upstream accepts a historical day, so both are always served as-of-latest, the same as
 * `strategyRecommendations` and `carbonPotential`.
 */
const DATE_PARAMETERISED_SOURCES: ReadonlySet<RegionalEvidenceSource> = new Set<
  RegionalEvidenceSource
>(["fireDetections", "streamflow", "drought", "weatherObservations"]);

/**
 * The warehouse stream whose coverage record answers for a block, when one does.
 *
 * `drought` earned an entry on 2026-08-09 and it is not cosmetic: it had none while it claimed
 * no capability at all, so every empty drought read on a past day resolved to
 * `coverage_unknown_on_viewed_date` -- honest, but the weakest thing we could say. The stream
 * capability dates a release by the days it COVERS rather than by the Tuesday it was valid on,
 * so the gap list here is answering the same question the map's own axis answers.
 */
const COVERAGE_LAYER_BY_EVIDENCE_SOURCE: Partial<
  Record<RegionalEvidenceSource, string>
> = {
  fireDetections: "fire-detections",
  firePerimeters: "fire-perimeters",
  streamflow: "water-gauges",
  weatherObservations: "weather-observations",
  drought: SLIDER_STREAM_LAYER_NAMES.drought,
};

function droughtClassAtPoint(
  collection: GeoJSON.FeatureCollection,
  latitude: number,
  longitude: number
): string | null {
  const labels = [
    "D0 (Abnormally Dry)",
    "D1 (Moderate Drought)",
    "D2 (Severe Drought)",
    "D3 (Extreme Drought)",
    "D4 (Exceptional Drought)",
  ];
  const highest = droughtLevelAtPoint(collection, latitude, longitude);
  return highest !== null ? labels[highest] ?? `D${highest}` : null;
}

function nearestGauge(
  gauges: WaterGauge[],
  latitude: number,
  longitude: number
): WaterGauge | null {
  let nearest: WaterGauge | null = null;
  let distance = Number.POSITIVE_INFINITY;
  for (const gauge of gauges) {
    const candidate = Math.hypot(gauge.lat - latitude, gauge.lon - longitude);
    if (candidate < distance) {
      nearest = gauge;
      distance = candidate;
    }
  }
  return distance <= NEAREST_GAUGE_MAX_DEGREES ? nearest : null;
}

function settled<T>(result: PromiseSettledResult<T>, fallback: T): T {
  return result.status === "fulfilled" ? result.value : fallback;
}

function readString(source: Record<string, unknown>, key: string): string | null {
  const value = source[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function readNumber(source: Record<string, unknown>, key: string): number | null {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Fire perimeters have no per-feature observation time upstream, so row
 * `updatedAt` is the only honest freshness signal available for them.
 */
async function readPublishedFirePerimeters(
  west: number,
  south: number,
  east: number,
  north: number
): Promise<{ perimeters: NearbyFirePerimeter[]; latestUpdatedAt: string | null }> {
  const rows = await db
    .select({ properties: features.properties, updatedAt: features.updatedAt })
    .from(features)
    .innerJoin(layers, eq(features.layerId, layers.id))
    .where(
      and(
        eq(layers.name, process.env.FIRES_LAYER_ID ?? "fire-perimeters"),
        eq(features.status, "published"),
        gte(sql<number>`ST_XMax(${features.geom})`, west),
        lte(sql<number>`ST_XMin(${features.geom})`, east),
        gte(sql<number>`ST_YMax(${features.geom})`, south),
        lte(sql<number>`ST_YMin(${features.geom})`, north)
      )
    )
    .orderBy(desc(features.updatedAt))
    .limit(MAX_FIRE_PERIMETERS);

  const perimeters: NearbyFirePerimeter[] = [];
  let latestUpdatedAt: string | null = null;
  for (const row of rows) {
    const properties =
      row.properties && typeof row.properties === "object"
        ? (row.properties as Record<string, unknown>)
        : null;
    if (!properties || !row.updatedAt) continue;
    const updatedAt = row.updatedAt.toISOString();
    latestUpdatedAt ??= updatedAt;
    perimeters.push({
      name:
        readString(properties, "name") ??
        readString(properties, "id") ??
        "Unnamed perimeter",
      irwinId: readString(properties, "irwinId"),
      updatedAt,
    });
  }
  return { perimeters, latestUpdatedAt };
}

const COMMUNITY_PROPOSAL_RADIUS_METERS = 10_000;
const MAX_COMMUNITY_PROPOSALS = 5;
/** The `geo.layers` row every intervention feature belongs to; mirrors `interventionsRouter`. */
const INTERVENTIONS_LAYER_NAME = "interventions";
/** Mirrors `interventionsRouter`'s `RECOMMENDATION_STATUS`: a submission still awaiting review. */
const COMMUNITY_PROPOSAL_STATUS = "pending_review";

/**
 * Nearby community intervention proposals still awaiting expert review.
 *
 * A direct read of `geo.features`/`geo.layers` rather than a call through
 * `interventionsRouter.listProposed`: that procedure is `protectedProcedure`-gated and reads
 * `ctx.session`, neither of which exists for a server-side context assembler with no inbound
 * tRPC request. The row shape and consent/status gate below are kept in lockstep with it by
 * hand -- see `src/lib/server/trpc/routers/interventions.ts`.
 *
 * Resolves to an empty array both when nothing is nearby and when the read itself fails (an
 * unprovisioned `interventions` layer, for instance): unlike a warehouse evidence plane, there is
 * no coverage-gap distinction to preserve here, so the caller cannot tell the two apart from this
 * return value alone and does not need to.
 */
async function readCommunityProposals(
  lat: number,
  lon: number
): Promise<CommunityProposal[]> {
  const [layer] = await db
    .select({ id: layers.id })
    .from(layers)
    .where(eq(layers.name, INTERVENTIONS_LAYER_NAME))
    .limit(1);
  if (!layer) return [];

  const point = sql`ST_SetSRID(ST_MakePoint(${lon}, ${lat}), 4326)::geography`;
  const rows = await db
    .select({
      id: features.id,
      name: sql<string | null>`${features.properties} ->> 'name'`,
      type: sql<string | null>`${features.properties} ->> 'type'`,
      description: sql<string | null>`${features.properties} ->> 'description'`,
      distanceMeters: sql<number>`ST_Distance(${features.geom}::geography, ${point})`,
      createdAt: features.createdAt,
    })
    .from(features)
    .where(
      and(
        eq(features.layerId, layer.id),
        eq(features.status, COMMUNITY_PROPOSAL_STATUS),
        sql`${features.properties} ->> 'publicationConsent' = 'true'`,
        sql`ST_DWithin(${features.geom}::geography, ${point}, ${COMMUNITY_PROPOSAL_RADIUS_METERS})`
      )
    )
    .orderBy(sql`ST_Distance(${features.geom}::geography, ${point})`)
    .limit(MAX_COMMUNITY_PROPOSALS);

  return rows.map((row) => ({
    id: row.id,
    name: row.name,
    type: row.type,
    description: row.description,
    distanceMeters: Math.round(Number(row.distanceMeters)),
    createdAt: (row.createdAt ?? new Date(0)).toISOString(),
  }));
}

const STRATEGY_CONTEXT_MATVIEW = "geo.mv_strategy_recommendations_regional";
const MAX_STRATEGY_CONTEXT_ENTRIES = 3;
/**
 * How far outside a cell's own boundary a point still counts as being in it.
 *
 * A tolerance, not a cell half-width. Until drizzle 0028 the regional matview drew fixed
 * `ST_MakeEnvelope(lon±0.125, lat±0.125)` boxes around fabricated centres, so half of one box
 * was a meaningful number; the tier now unions REAL `agri.spatial_cell` polygons, whose size is
 * whatever the source grid publishes and differs between grids. `ST_DWithin` against the real
 * polygon is already 0 for any point inside it, so this only widens the match to points just
 * outside a cell edge — near enough that answering about the neighbouring cell is better than
 * answering about nothing.
 */
const STRATEGY_MATVIEW_CELL_RADIUS_METERS = 14_000;

/** Object type, not an interface: db.execute requires an implicit index signature. */
type StrategyMatviewRow = {
  strategy_slug: string;
  strategy_name: string;
  strategy_category: string | null;
  suitability_score: number;
};

/** Whether a relation is present in this database. Used to gate on drizzle 0027/0028 (`geo.mv_strategy_recommendations_*`), which is not yet applied in every environment. */
async function relationExists(qualifiedName: string): Promise<boolean> {
  const rows = await db.execute<{ exists: boolean }>(
    sql`SELECT to_regclass(${qualifiedName}) IS NOT NULL AS exists`
  );
  return rows[0]?.exists === true;
}

/**
 * Top strategy candidates for this point.
 *
 * Governance boundary (do not relax without an owner decision): this repo forbids representing
 * strategy-model output as a causal effect claim. The `20260725_0013` causal plane is empty and
 * deliberately blocked, and today's evaluation model carries
 * `label_review_tier = agent_reviewed_pending_owner_signature` -- reviewed by an agent, not
 * signed off by an owner. See
 * `services/agri-data-service/src/agri_data_service/method/AGENTS.md`.
 *
 * `geo.mv_strategy_recommendations_*` carries an `effect_utility_score` and an
 * `effect_utility_lower`/`effect_utility_upper` spread. Until drizzle 0028 those three were
 * named `causal_benefit_tau`, `confidence_lower` and `confidence_upper` -- names asserting a
 * causal effect and a validated interval that this evidence chain has never been able to
 * support -- and they were computed over `agri.strategy_selection_candidate` rows assigned to
 * RANDOM coordinates (`37.5 + random() * 5.0`), so they described no place at all. 0028
 * rebuilds the plane over the real `agri.spatial_cell` geometry each candidate's analysis
 * subject actually occupies and renames the three columns to what they are: a model-internal
 * ordering quantity and its spread.
 *
 * They are still never read here, and the renaming does not relax the boundary. Only
 * `suitability_score` -- a relative ranking, not an effect size -- crosses it, and every entry
 * is tagged with the tier that produced it so the prompt and the UI can say so.
 */
async function resolveStrategyContext(
  lat: number,
  lon: number,
  heuristicRecommendations: StrategyScore[]
): Promise<StrategyContextEntry[]> {
  const matviewPresent = await relationExists(STRATEGY_CONTEXT_MATVIEW).catch(() => false);

  if (matviewPresent) {
    const point = sql`ST_SetSRID(ST_MakePoint(${lon}, ${lat}), 4326)::geography`;
    const rows = await db.execute<StrategyMatviewRow>(sql`
      SELECT strategy_slug, strategy_name, strategy_category, suitability_score
      FROM ${sql.raw(STRATEGY_CONTEXT_MATVIEW)}
      WHERE ST_DWithin(geom::geography, ${point}, ${STRATEGY_MATVIEW_CELL_RADIUS_METERS})
      ORDER BY suitability_score DESC
      LIMIT ${MAX_STRATEGY_CONTEXT_ENTRIES}
    `);
    return rows.map((row) => ({
      claimTier: "evaluation_only_model" as const,
      strategySlug: row.strategy_slug,
      name: row.strategy_name,
      category: row.strategy_category,
      score: row.suitability_score,
    }));
  }

  return heuristicRecommendations.slice(0, MAX_STRATEGY_CONTEXT_ENTRIES).map((score) => ({
    claimTier: "heuristic_score" as const,
    strategySlug: score.strategyId,
    name: score.name,
    category: null,
    score: score.score,
  }));
}

/**
 * The nearest published weather sample to the point, for the live edge or for one named day.
 *
 * The live path stays on `getPublishedWeatherForPoint`'s unbounded nearest-1 KNN, untouched:
 * it is the first paint of every session and its cost is already measured. A named past day
 * has no point reader at all, so it goes through the bbox reader and the nearest sample is
 * picked here. That difference is real and is reported rather than hidden -- on a past day
 * this only sees the context window, so an empty result means "nothing within
 * CONTEXT_RADIUS_DEGREES of the point", not "nothing anywhere".
 */
async function readNearestWeather(
  lat: number,
  lon: number,
  bbox: string,
  date: string | undefined,
  today: string
): Promise<PublishedWeatherObservation | null> {
  if (resolveRequestedObservationDay(date, today).kind !== "historical") {
    return getPublishedWeatherForPoint(lat, lon);
  }
  let nearest: PublishedWeatherObservation | null = null;
  let distance = Number.POSITIVE_INFINITY;
  for (const observation of await getPublishedWeatherForBbox(bbox, date)) {
    const candidate = Math.hypot(observation.lat - lat, observation.lon - lon);
    if (candidate < distance) {
      nearest = observation;
      distance = candidate;
    }
  }
  return nearest;
}

/** Whether a layer's own coverage record shows a day as published. */
type CoverageOnDay =
  | { state: "published" }
  | { state: "not_published"; reason: string }
  | { state: "unknown"; reason: string };

/**
 * Reads one day out of a layer's server-side coverage record.
 *
 * This is what separates "the warehouse published nothing that day" from "it published, and
 * there was nothing here". An empty read alone cannot tell them apart, and the client's
 * `hasDataOnDate` is a claim rather than evidence, so the authority is the layer's own
 * `coverageGaps` -- the same record the slider draws its holes from.
 */
function coverageOnDay(
  capabilityLayers: ResolvedSliderLayerCapability[] | null,
  source: RegionalEvidenceSource,
  date: string
): CoverageOnDay {
  const layerName = COVERAGE_LAYER_BY_EVIDENCE_SOURCE[source];
  if (layerName === undefined) {
    return {
      state: "unknown",
      reason: `${source} has no geo.layers row, so no coverage record can say whether ${date} was ingested.`,
    };
  }
  if (capabilityLayers === null) {
    return {
      state: "unknown",
      reason: "The layer coverage record could not be read for this request.",
    };
  }
  const capability = capabilityLayers.find((layer) => layer.layerName === layerName);
  if (capability === undefined || capability.earliestObservedDate === null) {
    return {
      state: "unknown",
      reason: `${layerName} publishes no coverage record to check ${date} against.`,
    };
  }
  if (date < capability.earliestObservedDate) {
    return {
      state: "not_published",
      reason: `${layerName} has no observations before ${capability.earliestObservedDate}.`,
    };
  }
  // Guarded rather than trusted: a capability shaped before coverageGaps existed carries no
  // array, and a missing gap list must read as "no gap known", never as a crash.
  const gap = (capability.coverageGaps ?? []).find(
    (range) => date >= range.from && date <= range.to
  );
  // A LISTED gap is positive evidence whatever the boundary says, so it is decided first.
  if (gap !== undefined) {
    return {
      state: "not_published",
      reason: `${layerName} published nothing from ${gap.from} through ${gap.to}.`,
    };
  }
  // Absence from that list is only evidence where the list SPEAKS. The read model caps both
  // range lists at the newest MAX_REPORTED_DAY_RANGES entries and reports the boundary in
  // `describedFromDay`; below it a dropped gap is indistinguishable from continuous coverage.
  // Reading the list anyway is exactly how a never-ingested day reached
  // `published_with_nothing_at_this_location` and licensed the agent's strongest sentence --
  // "This is an observed absence: you may say there was none here" -- for a day nobody observed.
  if (!isDayDescribed(capability, date)) {
    return {
      state: "unknown",
      reason:
        `${layerName} reports its coverage only from ${capability.describedFromDay} onward, ` +
        `so nothing on record says whether ${date} was ingested.`,
    };
  }
  return { state: "published" };
}

/**
 * Whether the MAP draws this row bounded by its own viewed day.
 *
 * Style-baked tile layers are the only ones that can disagree with their payload block, because
 * they are filtered in the style rather than read per day: `applyDateFilter` in LayerManager
 * installs `["<=", ["get","observed_day"], day]` on exactly the toggles that are both listed
 * as date-filterable AND have a selectable day. Asked here through the same `hasSelectableDay`
 * the client asks, against the same capabilities payload the client was served, so this cannot
 * describe a filter the browser did not install.
 */
function mapBoundsRowByViewedDay(
  layer: string,
  capabilities: ResolvedSliderCapabilities | null
): boolean {
  const toggleId: LayerToggleId | null = isLayerToggleId(layer)
    ? layer
    : toggleIdForWarehouseLayerName(layer);
  if (toggleId === null) return false;
  if (!DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS.includes(toggleId)) return false;
  return hasSelectableDay(capabilities, toggleId);
}

/** Whether the block a row feeds holds the set the map draws for it. */
function resolveSetCorrespondence(
  layer: string,
  evidenceSource: RegionalEvidenceSource | null,
  capabilities: ResolvedSliderCapabilities | null
): ViewedLayerSetCorrespondence {
  if (evidenceSource === null) return "no_payload_block_for_this_row";
  // A block read AT the viewed day describes the day its row draws, whatever that read found.
  if (DATE_PARAMETERISED_SOURCES.has(evidenceSource)) return "payload_is_the_viewed_day";
  return mapBoundsRowByViewedDay(layer, capabilities)
    ? "map_bounded_by_viewed_day_payload_is_latest"
    : "map_unbounded_payload_is_latest";
}

/** Whether one payload block's read completed, and whether it returned anything. */
interface SourceReadState {
  failed: boolean;
  hasObservations: boolean;
}

/**
 * Assembles published warehouse observations for the agent. Sources that have
 * not been published resolve to `unavailable` in `dataFreshness` rather than
 * blocking the request, so the agent can still answer while saying what it
 * could not see.
 *
 * `viewedLayers` names the day each map row is scrubbed to. Blocks in
 * DATE_PARAMETERISED_SOURCES are read AT that day; every other block is still read at the
 * live edge and is reported as `served_as_of_latest` so the agent cannot attribute a live
 * value to a day the user is looking at. An omitted or empty list reads exactly as this
 * function always has.
 */
export async function assembleRegionalContext(
  lat: number,
  lon: number,
  viewedLayers: ViewedLayerRequest[] = []
): Promise<RegionalContextResult> {
  const west = Math.max(-180, lon - CONTEXT_RADIUS_DEGREES);
  const south = Math.max(-90, lat - CONTEXT_RADIUS_DEGREES);
  const east = Math.min(180, lon + CONTEXT_RADIUS_DEGREES);
  const north = Math.min(90, lat + CONTEXT_RADIUS_DEGREES);
  const bbox = `${west},${south},${east},${north}`;
  const today = serverCurrentDate();

  // One day per block, last row wins. Two rows keyed to the same block -- a toggle id and its
  // warehouse alias -- is a client bug, not a reason to run the same warehouse read twice.
  const dateBySource = new Map<RegionalEvidenceSource, string>();
  for (const row of viewedLayers) {
    const source = EVIDENCE_SOURCE_BY_VIEWED_LAYER[row.layer];
    if (source !== undefined) dateBySource.set(source, row.date);
  }

  const [
    strategy,
    drought,
    gauges,
    weather,
    fires,
    perimeters,
    carbon,
    capabilities,
    soil,
    mtbs,
    communityProposals,
  ] = await Promise.allSettled([
    getStrategyRecommendations(lat, lon),
    getPublishedDroughtClassification(undefined, dateBySource.get("drought")),
    getPublishedStreamflowGauges(bbox, dateBySource.get("streamflow")),
    readNearestWeather(
      lat,
      lon,
      bbox,
      dateBySource.get("weatherObservations"),
      today
    ),
    // The middle argument is the FIRMS lookback window; passing undefined takes its default,
    // which is the same window this call has always used.
    getPublishedFireDetections(bbox, undefined, dateBySource.get("fireDetections")),
    readPublishedFirePerimeters(west, south, east, north),
    getInterventionSuitability(lat, lon),
    getSliderCapabilities(),
    // Live external reads, added 2026-08-14 to replace the two fields this assembler used to
    // hardcode to null despite both having a real server-side read path.
    getSoilProperties(lat, lon),
    getMTBSPerimeters(bbox),
    readCommunityProposals(lat, lon),
  ]);

  const strategyValues = settled(strategy, [] as StrategyScore[]);
  const droughtValue = drought.status === "fulfilled" ? drought.value : null;
  const gaugeValues = settled(gauges, [] as WaterGauge[]);
  const weatherValue = weather.status === "fulfilled" ? weather.value : null;
  const fireCollection = settled(fires, {
    type: "FeatureCollection",
    features: [],
  } as GeoJSON.FeatureCollection<GeoJSON.Point>);
  const perimeterValue = settled(perimeters, {
    perimeters: [] as NearbyFirePerimeter[],
    latestUpdatedAt: null as string | null,
  });
  const carbonValue = carbon.status === "fulfilled" ? carbon.value : null;
  const soilValue = soil.status === "fulfilled" ? soil.value : null;
  const mtbsCollection = settled(mtbs, {
    type: "FeatureCollection",
    features: [],
  } as GeoJSON.FeatureCollection);
  const communityProposalsValue = settled(
    communityProposals,
    [] as CommunityProposal[]
  );
  // Runs after the batch above resolves rather than inside it: the matview branch is a second
  // sequential query only when drizzle 0027/0028 is actually applied, which today it is not
  // anywhere this runs -- so the common path adds no extra latency, just a map over
  // `strategyValues`.
  const strategyContextValue = await resolveStrategyContext(
    lat,
    lon,
    strategyValues
  ).catch(() => [] as StrategyContextEntry[]);

  const latestMtbsIgnitionDate = mtbsCollection.features
    .map((feature) => {
      const properties = feature.properties as MTBSFireProperties | null;
      return typeof properties?.ignitionDate === "string" ? properties.ignitionDate : null;
    })
    .filter((value): value is string => value !== null && Number.isFinite(Date.parse(value)))
    .sort()
    .at(-1);

  const detections: NearbyFireDetection[] = [];
  for (const feature of fireCollection.features) {
    const properties = (feature.properties ?? {}) as Record<string, unknown>;
    const observedAt = readString(properties, "observedAt");
    if (!observedAt) continue;
    detections.push({
      observedAt,
      lon: feature.geometry.coordinates[0],
      lat: feature.geometry.coordinates[1],
      confidence: readString(properties, "confidence"),
      frp: readNumber(properties, "frp"),
    });
    if (detections.length >= MAX_FIRE_DETECTIONS) break;
  }
  const latestDetectionAt = detections
    .map((detection) => detection.observedAt)
    .sort()
    .at(-1);

  const dataFreshness: Record<string, string> = {
    drought:
      droughtValue?.availability === "published" && droughtValue.observedAt
        ? droughtValue.observedAt
        : "unavailable",
    streamflow:
      gaugeValues
        .map((gauge) => gauge.updatedAt)
        .filter((value) => Number.isFinite(Date.parse(value)))
        .sort()
        .at(-1) ?? "unavailable",
    weatherObservations: weatherValue?.observedAt ?? "unavailable",
    fireDetections: latestDetectionAt ?? "unavailable",
    firePerimeters: perimeterValue.latestUpdatedAt ?? "unavailable",
    strategyRecommendations:
      strategyValues.length > 0 ? "published_revision_required" : "unavailable",
    // SoilGrids v2.0 is a static, undated raster release (see soilgrids.ts): there is no
    // per-request observation time to report, so this sentinel is deliberately not a parseable
    // date. It still resolves `contextIsEmpty` and the freshness footer correctly to "available
    // data exists" vs "unavailable" -- the one thing it cannot claim is a specific age.
    soilProperties: soilValue !== null ? "static_release_untimed" : "unavailable",
    mtbsPerimeters: latestMtbsIgnitionDate ?? "unavailable",
    carbonPotential:
      carbonValue?.availability === "published"
        ? "published_revision_required"
        : "unavailable",
  };

  const payload: RegionalContextPayload = {
    location: { lat, lon, geohash: `${lat.toFixed(2)}_${lon.toFixed(2)}` },
    strategyRecommendations: strategyValues.length > 0 ? strategyValues : null,
    strategyContext: strategyContextValue,
    communityProposals: communityProposalsValue,
    soilProperties: soilValue,
    waterScarcity:
      droughtValue?.availability === "published" || gaugeValues.length > 0
        ? {
            droughtClass: droughtValue
              ? droughtClassAtPoint(droughtValue, lat, lon)
              : null,
            nearestGauge: nearestGauge(gaugeValues, lat, lon),
          }
        : null,
    weather: weatherValue,
    fireDetections: detections.length
      ? { detections, totalCount: fireCollection.features.length }
      : null,
    firePerimeters: perimeterValue.perimeters.length
      ? {
          perimeters: perimeterValue.perimeters,
          totalCount: perimeterValue.perimeters.length,
        }
      : null,
    mtbsPerimeters: mtbsCollection.features.length
      ? {
          fires: mtbsCollection.features.slice(0, MAX_MTBS_FIRES),
          totalCount: mtbsCollection.features.length,
        }
      : null,
    carbonPotential:
      carbonValue?.availability === "published" ? carbonValue : null,
  };

  const contextIsEmpty = Object.values(dataFreshness).every(
    (value) => value === "unavailable"
  );

  // A rejection and an empty result are separate facts and stay separate all the way to the
  // prompt. Only the blocks a viewed row can name are tracked: `strategyRecommendations`
  // rejects with StrategyEvidenceUnavailableError on its ordinary unavailable path, so calling
  // that a failed read would be its own small lie.
  const readState: Partial<Record<RegionalEvidenceSource, SourceReadState>> = {
    fireDetections: {
      failed: fires.status === "rejected",
      hasObservations: detections.length > 0,
    },
    firePerimeters: {
      failed: perimeters.status === "rejected",
      hasObservations: perimeterValue.perimeters.length > 0,
    },
    streamflow: {
      failed: gauges.status === "rejected",
      hasObservations: gaugeValues.length > 0,
    },
    drought: {
      failed: drought.status === "rejected",
      hasObservations:
        droughtValue?.availability === "published" &&
        droughtValue.features.length > 0,
    },
    weatherObservations: {
      failed: weather.status === "rejected",
      hasObservations: weatherValue !== null,
    },
  };

  // The whole payload rather than just its layer list: `mapBoundsRowByViewedDay` asks
  // `hasSelectableDay`, which needs `serverCurrentDate` and `futureAxisDays` to answer the same
  // way the browser did.
  const capabilityPayload =
    capabilities.status === "fulfilled" ? capabilities.value : null;

  const readings = viewedLayers.map((row) =>
    resolveViewedLayerReading(row, today, readState, capabilityPayload)
  );
  const namedSources = new Set(
    readings
      .map((reading) => reading.evidenceSource)
      .filter((source): source is RegionalEvidenceSource => source !== null)
  );

  const temporalContext: TemporalContext = {
    serverCurrentDate: today,
    viewedLayersUnreported: viewedLayers.length === 0,
    readings,
    viewedDates: [...new Set(viewedLayers.map((row) => row.date))].sort(),
    sourcesServedAsOfLatest: REGIONAL_EVIDENCE_SOURCES.filter(
      (source) => !namedSources.has(source)
    ),
  };

  return { payload, dataFreshness, contextIsEmpty, temporalContext, cacheHit: false };
}

/**
 * Resolves one viewed row against what the server's own read of that day did.
 *
 * The order of the branches is the point. A failed read is decided before an empty one, and an
 * empty one is decided against the layer's coverage record before anything is said about it,
 * so no path can turn "we do not know" into "nothing was there".
 */
function resolveViewedLayerReading(
  row: ViewedLayerRequest,
  today: string,
  readState: Partial<Record<RegionalEvidenceSource, SourceReadState>>,
  capabilities: ResolvedSliderCapabilities | null
): ViewedLayerReading {
  const capabilityLayers = capabilities?.layers ?? null;
  const evidenceSource = EVIDENCE_SOURCE_BY_VIEWED_LAYER[row.layer] ?? null;
  const base = {
    layer: row.layer,
    viewedDate: row.date,
    clientReportsDataOnDate: row.hasDataOnDate,
    evidenceSource,
    clientClaimContradicted: false,
    // On `base` so every branch below carries it: whether the agent holds the drawn set is a
    // property of the WIRING, not of what this particular read happened to find, and a branch
    // that returned early without it would silently tell the model the sets correspond.
    setCorrespondence: resolveSetCorrespondence(row.layer, evidenceSource, capabilities),
  };

  if (evidenceSource === null) {
    return {
      ...base,
      outcome: "not_represented_in_payload",
      reason: `No observation block here is fed by the "${row.layer}" layer.`,
    };
  }
  if (!DATE_PARAMETERISED_SOURCES.has(evidenceSource)) {
    return {
      ...base,
      outcome: "served_as_of_latest",
      reason: `${evidenceSource} has no per-day reader, so its values are the latest published ones.`,
    };
  }

  const day = resolveRequestedObservationDay(row.date, today);
  if (day.kind === "unobserved") {
    return { ...base, outcome: "viewed_date_not_observable", reason: day.reason };
  }

  const state = readState[evidenceSource];
  if (state === undefined) {
    return {
      ...base,
      outcome: "coverage_unknown_on_viewed_date",
      reason: `${evidenceSource} was not read for this request.`,
    };
  }
  if (state.failed) {
    return {
      ...base,
      outcome: "read_failed",
      reason: `The ${evidenceSource} read did not complete for this request.`,
    };
  }
  if (state.hasObservations) {
    return { ...base, outcome: "observed_on_viewed_date", reason: null };
  }

  const coverage = coverageOnDay(capabilityLayers, evidenceSource, row.date);
  switch (coverage.state) {
    case "published":
      return {
        ...base,
        outcome: "published_with_nothing_at_this_location",
        reason: null,
        clientClaimContradicted: !row.hasDataOnDate,
      };
    case "not_published":
      return {
        ...base,
        outcome: "not_published_on_viewed_date",
        reason: coverage.reason,
        clientClaimContradicted: row.hasDataOnDate,
      };
    case "unknown":
      return {
        ...base,
        outcome: "coverage_unknown_on_viewed_date",
        reason: coverage.reason,
      };
  }
}
