import { z } from "zod";
import { soilPropertyForToggle, SOIL_RASTER_TOGGLE_IDS } from "@/lib/map/soil-raster";
import type { BoundingBox } from "@/lib/server/security/bbox";

export const APP_MAP_SURFACES = [
  "demand-heatmap",
  ...SOIL_RASTER_TOGGLE_IDS,
  "interventions",
  "strategy-recommendations",
  "land-context",
  "fire-risk",
  "weather-forecast",
] as const;

const calendarDay = z.string().date();
const selectionSchema = z.object({
  surface_name: z.enum(APP_MAP_SURFACES),
  longitude: z.number().finite().min(-180).max(180),
  latitude: z.number().finite().min(-90).max(90),
  day: calendarDay,
  range_start: calendarDay,
  range_end: calendarDay,
  time_scale: z.enum(["day", "week", "month", "year", "all"]).default("day"),
  zoom: z.number().finite().min(0).max(24).default(13),
  page_start: z.number().int().nonnegative().default(0),
}).strict().refine((value) => value.range_start <= value.day && value.day <= value.range_end, {
  message: "comparison window must contain the selected day",
});

type Selection = z.infer<typeof selectionSchema>;
type Evidence = Record<string, unknown>;
const MAX_FEATURES = 50;
const MERCATOR_LIMIT = 85.0511287798066;

/** Resolve the Web Mercator tile that contains the coordinate, including world edges. */
export function selectionTile(longitude: number, latitude: number, zoom: number) {
  if (![longitude, latitude, zoom].every(Number.isFinite)
    || longitude < -180 || longitude > 180 || Math.abs(latitude) > MERCATOR_LIMIT) return null;
  const z = Math.min(22, Math.max(0, Math.floor(zoom)));
  const size = 2 ** z;
  const x = Math.min(size - 1, Math.max(0, Math.floor((longitude + 180) / 360 * size)));
  const radians = latitude * Math.PI / 180;
  const y = Math.min(size - 1, Math.max(0, Math.floor((1 - Math.asinh(Math.tan(radians)) / Math.PI) / 2 * size)));
  const latitudeAt = (row: number) => Math.atan(Math.sinh(Math.PI * (1 - 2 * row / size))) * 180 / Math.PI;
  const bbox: BoundingBox = [x / size * 360 - 180, latitudeAt(y + 1), (x + 1) / size * 360 - 180, latitudeAt(y)];
  return { z, x, y, bbox };
}

export function isAppMapSurface(name: unknown): name is (typeof APP_MAP_SURFACES)[number] {
  return typeof name === "string" && (APP_MAP_SURFACES as readonly string[]).includes(name);
}

/** Bound application reads by both the caller's cancellation and the shared tool deadline. */
export async function readBoundedAppMapEvidence(args: Record<string, unknown>, signal?: AbortSignal): Promise<Evidence> {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    };
    const fail = (error: unknown) => { cleanup(); reject(error); };
    const abort = () => fail(signal?.reason ?? new Error("Map evidence read cancelled"));
    const timer = setTimeout(() => fail(new Error("Map evidence read exceeded its deadline")), 12_000);
    signal?.addEventListener("abort", abort, { once: true });
    void readAppMapEvidence(args).then((result) => { cleanup(); resolve(result); }, fail);
  });
}

function envelope(selection: Selection, selected: Evidence, extra: Evidence = {}): Evidence {
  return {
    surface_name: selection.surface_name,
    requested_day: selection.day,
    selection: { ...selection, tile: selectionTile(selection.longitude, selection.latitude, selection.zoom) },
    history: {
      range_start: selection.range_start,
      range_end: selection.range_end,
      sampled_days: [],
      sampled_day_count: 0,
      complete: false,
      next_page_start: null,
      state: "historical_snapshots_not_published",
    },
    lanes: [{
      serving_reader: "map_application",
      lane_nature: "current_snapshot",
      selected: { requested_day: selection.day, served_day: null, features: [], ...selected },
      history: [],
    }],
    ...extra,
  };
}

async function rasterEvidence(selection: Selection): Promise<Evidence> {
  const { getPublishedSoilRasters } = await import("./raster-catalog");
  const [{ db }, { sql }] = await Promise.all([import("@/lib/server/db"), import("drizzle-orm")]);
  const property = soilPropertyForToggle(selection.surface_name);
  const rasters = await db.transaction(async (transaction) => {
    await transaction.execute(sql`SET LOCAL statement_timeout = '10s'`);
    return getPublishedSoilRasters(transaction);
  });
  const raster = rasters.find((entry) => entry.property === property);
  const tile = raster ? selectionTile(selection.longitude, selection.latitude, Math.min(selection.zoom, raster.maxZoom)) : null;
  const withinBounds = raster && selection.longitude >= raster.bounds[0] && selection.longitude <= raster.bounds[2]
    && selection.latitude >= raster.bounds[1] && selection.latitude <= raster.bounds[3];
  return envelope(selection, {
    state: !raster ? "raster_release_not_published" : !withinBounds ? "outside_release_coverage" : "numeric_values_unavailable",
  }, {
    raster_publication: raster ? { ...raster, tile, numeric_values_available: false } : null,
    note: "The map serves a rendered SoilGrids raster release. Its legend and release bounds are metadata, "
      + "not a value at the selected coordinate. Numeric source values and historical raster releases are not "
      + "admitted by the current serving contract; do not infer measurements from raster colours or legend limits.",
  });
}

async function publishedInterventions(tile: NonNullable<ReturnType<typeof selectionTile>>): Promise<Evidence[]> {
  const [{ db }, { sql }] = await Promise.all([import("@/lib/server/db"), import("drizzle-orm")]);
  const [west, south, east, north] = tile.bbox;
  const rows = await db.transaction(async (transaction) => {
    await transaction.execute(sql`SET LOCAL statement_timeout = '10s'`);
    return transaction.execute<Evidence>(sql`
    SELECT f.id, ST_AsGeoJSON(f.geom)::json AS geometry,
           jsonb_build_object(
             'type', f.properties ->> 'type',
             'intervention_type', coalesce(f.properties ->> 'type', f.properties ->> 'intervention_type'),
             'kind', f.properties ->> 'kind', 'category', f.properties ->> 'category',
             'priority', f.properties ->> 'priority', 'status', f.properties ->> 'status',
             'name', f.properties ->> 'name', 'description', f.properties ->> 'description',
             'created_at', f.created_at, 'updated_at', f.updated_at
           ) AS properties
      FROM geo.features f JOIN geo.layers l ON l.id = f.layer_id
     WHERE l.name = 'interventions' AND l.is_public IS TRUE AND f.status = 'published'
       AND f.geom IS NOT NULL
       AND f.geom && ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)
       AND ST_Intersects(f.geom, ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326))
     ORDER BY f.id LIMIT ${MAX_FEATURES + 1}
    `);
  });
  return rows.map((row) => ({ type: "Feature", ...row }));
}

/** Read app-owned map sources without promoting a current snapshot to historical evidence. */
export async function readAppMapEvidence(args: Record<string, unknown>, now = new Date()): Promise<Evidence> {
  const parsed = selectionSchema.safeParse(args);
  if (!parsed.success) return { error: "invalid_selection", detail: parsed.error.issues };
  const selection = parsed.data;
  const tile = selectionTile(selection.longitude, selection.latitude, selection.zoom);
  if (!tile) return envelope(selection, { state: "outside_web_mercator" });
  if (soilPropertyForToggle(selection.surface_name)) return rasterEvidence(selection);
  if (["strategy-recommendations", "fire-risk", "weather-forecast"].includes(selection.surface_name)) {
    return envelope(selection, { state: "forecast_parquet_lane_not_published" });
  }
  const today = now.toISOString().slice(0, 10);
  if (selection.day !== today) {
    return envelope(selection, { state: "historical_snapshot_unavailable" }, {
      note: "This map layer exposes current published application state only. No snapshot exists for the requested day.",
    });
  }
  let features: Evidence[];
  let metadata: Evidence;
  if (selection.surface_name === "land-context") {
    const { readBoundedAoiIntersection } = await import("./land-context");
    const [west, south, east, north] = tile.bbox;
    const result = await readBoundedAoiIntersection({ west, south, east, north }, { maxFeatures: MAX_FEATURES });
    const matched = result.status === "ok" ? result.data.filter((entry) => entry.sourceFeature !== null) : [];
    const coverageState = result.status === "ok" ? result.data[0]?.coverageState : result.status;
    return envelope(selection, {
      state: matched.length > 0 ? "published" : "refused",
      ...(matched.length === 0 ? { refusal_code: coverageState ?? "boundary_coverage_unavailable" } : {}),
      served_day: matched.length > 0 ? today : null,
      snapshot_as_of: now.toISOString(),
      temporal_semantics: "current_boundary_release_not_daily_observation",
      features: matched.map((entry) => ({ properties: entry })),
      coverage: result,
      spatial_basis: "boundary_features_intersecting_selected_tile",
    });
  } else if (selection.surface_name === "demand-heatmap") {
    const [{ db }, { aggregateActivityGrid, activityGridToFeatureCollection }] = await Promise.all([
      import("@/lib/server/db"), import("./community-activity"),
    ]);
    const { sql } = await import("drizzle-orm");
    const grid = await db.transaction(async (transaction) => {
      await transaction.execute(sql`SET LOCAL statement_timeout = '10s'`);
      return aggregateActivityGrid(transaction, {
        boundingBox: tile.bbox, zoom: tile.z, limit: MAX_FEATURES + 1, minimumVotes: 0, minimumFeatureCount: 3,
      });
    });
    features = activityGridToFeatureCollection(grid).features.map((feature) => ({ ...feature }));
    metadata = { cell_degrees: grid.cellDegrees, newest_contribution_at: grid.observedAt?.toISOString() ?? null,
      minimum_cell_members: 3, spatial_basis: "whole_aggregate_cells_intersecting_selected_tile" };
  } else {
    features = await publishedInterventions(tile);
    metadata = { spatial_basis: "published_features_intersecting_selected_tile", access_scope: "public_published_only" };
  }
  return envelope(selection, {
    state: "published", served_day: today, snapshot_as_of: now.toISOString(),
    temporal_semantics: "current_application_snapshot_not_daily_observation",
    features: features.slice(0, MAX_FEATURES), truncated: features.length > MAX_FEATURES, ...metadata,
  }, {
    note: "Current public map state in the containing tile; publication or contribution timestamps are not "
      + "environmental observation dates. Empty or suppressed community cells do not prove no activity. "
      + "Private drafts and review submissions are outside this public reader.",
  });
}
