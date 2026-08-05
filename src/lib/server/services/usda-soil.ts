import { sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { fetchBoundedJson } from "@/lib/server/http/bounded-upstream";
import { cacheGeoJSON, getCachedGeoJSON } from "@/lib/server/redis";

/** TTL 24 hours — SSURGO is republished on an annual cycle. */
const USDA_CACHE_TTL = 60 * 60 * 24;

/**
 * USDA Soil Data Access tabular SQL endpoint. Geometry and ratings both come from
 * here; see `src/lib/server/AGENTS.md` §soil-survey for why the SSURGO WFS cannot.
 */
const SDA_TABULAR_ENDPOINT =
  "https://SDMDataAccess.nrcs.usda.gov/Tabular/post.rest";

const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;

/** Clears the densest measured CONUS viewport (14.0 s), not the Boise one — see AGENTS.md. */
const REQUEST_TIMEOUT_MS = 30_000;

/** Map-unit polygons served per viewport; one more is requested so truncation is detectable. */
export const MAX_SOIL_POLYGONS = 1000;

/**
 * Viewport ceiling in square degrees, measured rather than guessed — see
 * `src/lib/server/AGENTS.md` §soil-survey for the two sites it was measured over. Also
 * the aggregation tiling cell size (§soil-survey-zoom below): it is the largest area a
 * single SDA round trip has been measured safe over, so no tier — detail or aggregated
 * — ever asks SDA for more than this in one call.
 */
export const MAX_SOIL_BBOX_SQUARE_DEGREES = 0.02;

export interface SoilSurveyProperties {
  mukey: string;
  muname: string | null;
  soilSeries: string | null;
  drainageClass: string | null;
  /** Tri-state: SSURGO rates a component Yes or No, or leaves it unranked. */
  hydric: boolean | null;
  landCapabilityClass: string | null;
}

/**
 * A cell's real map units merged by drainage class rather than drawn individually.
 * Never carries `mukey`/`muname`/`soilSeries`: this shape is not one surveyed map
 * unit, and must not be captioned as though it were. See §soil-survey-zoom.
 */
export interface AggregatedSoilSurveyProperties {
  aggregated: true;
  drainageClass: string;
  /** Real SSURGO map units merged into this shape — the count behind the average. */
  mapUnitCount: number;
  /** Share of the merged units SSURGO rated hydric; null when none carried a rating. */
  hydricFraction: number | null;
}

/**
 * "detail" draws real per-map-unit SSURGO polygons (unchanged behavior). The two
 * coarser bands draw `AggregatedSoilSurveyProperties` shapes instead — see
 * `resolveSoilSurveyGranularity`.
 */
export type SoilSurveyGranularity = "detail" | "regional-average" | "coarse-average";

export interface SoilSurveyCollection extends GeoJSON.FeatureCollection {
  /**
   * Upstream held more map units than were served (detail tier), or the aggregation
   * cell budget covered less than the requested viewport (aggregated tiers). Either
   * way, the view is a subset.
   */
  truncated: boolean;
  /**
   * Rows SDA served whose geometry this reader could not parse and therefore dropped.
   * Non-zero means the view is incomplete for a reason USDA never reported, so it must
   * not be presented as an absence of soil — the same honest gap `truncated` reports.
   */
  unreadableGeometries: number;
  granularity: SoilSurveyGranularity;
}

/**
 * §soil-survey-zoom — the owner's call: real per-map-unit polygons at high zoom,
 * progressively coarser drainage-class averages as the viewport (and its area) grows,
 * and never a hard viewport-too-big error once a caller supplies zoom.
 *
 * Thresholds are anchored to `viewportBbox()`'s own degrees-per-pixel formula
 * (`src/lib/map/viewport-bbox.ts`), not guessed: a default 1024x512px viewport's area
 * is `(360/2^(zoom+8))^2 * 524288` sq deg, which crosses `MAX_SOIL_BBOX_SQUARE_DEGREES`
 * at zoom ≈ 12.8 — the exact wall ordinary panning was hitting. "detail" starts at 13,
 * just above that crossing.
 */
export const SOIL_SURVEY_DETAIL_MIN_ZOOM = 13;

/** Below this, the coarser of the two averaged bands applies. */
export const SOIL_SURVEY_REGIONAL_MIN_ZOOM = 9;

/**
 * No zoom means a legacy caller — keep the original single-shot, hard-capped
 * behavior exactly as it was rather than guessing which tier it meant.
 */
export function resolveSoilSurveyGranularity(zoom?: number): SoilSurveyGranularity {
  if (zoom === undefined || !Number.isFinite(zoom)) return "detail";
  if (zoom >= SOIL_SURVEY_DETAIL_MIN_ZOOM) return "detail";
  if (zoom >= SOIL_SURVEY_REGIONAL_MIN_ZOOM) return "regional-average";
  return "coarse-average";
}

/**
 * The viewport-area ceiling the tRPC input schema enforces before this service ever
 * runs (see the diff handed to `environmental.ts`'s owner). A detail-tier request
 * makes exactly one uncapped-by-tiling SDA call, so it keeps the original measured
 * ceiling. An aggregated request tiles itself inside that same per-cell ceiling
 * (`fetchAggregatedCollection`) and degrades to `truncated: true` instead of erroring
 * when the viewport outgrows its cell budget, so it carries no ceiling at the schema
 * level at all.
 */
export function soilSurveyAreaCeiling(zoom?: number): number | null {
  return resolveSoilSurveyGranularity(zoom) === "detail" ? MAX_SOIL_BBOX_SQUARE_DEGREES : null;
}

/**
 * Sub-cells tiled (and merged) per aggregated request, per side of the grid. Bounds
 * upstream SDA calls at a viewport that would otherwise ask for an area this service
 * has never measured safe: 3x3 covers 9x `MAX_SOIL_BBOX_SQUARE_DEGREES`, tiled rather
 * than asked for in one call because SDA's own per-call cost is what the ceiling
 * protects, not just the bytes returned.
 */
export const MAX_SOIL_AGGREGATION_CELLS_PER_SIDE = 3;

/** SDA requests in flight at once for one aggregated response. Bounded independently
 * of the cell budget so a 3x3 grid cannot fan out 9 requests simultaneously against a
 * shared, rate-sensitive upstream. */
const AGGREGATION_CONCURRENCY = 3;

/** Simplify tolerance (degrees) for the finer averaged band. */
const REGIONAL_SIMPLIFY_TOLERANCE_DEGREES = 0.0015;

/** Simplify tolerance (degrees) for the coarser averaged band. */
const COARSE_SIMPLIFY_TOLERANCE_DEGREES = 0.005;

/** Soil Data Access answered, but not with a result table this module can read. */
export class SoilSurveyResponseError extends Error {}

/** Column aliases the query sets and the reader looks up by name. */
const MUKEY_COLUMN = "mukey";
const GEOMETRY_COLUMN = "geom";

/**
 * SSURGO map units for the viewport, at the granularity `zoom` resolves to (see
 * §soil-survey-zoom above `resolveSoilSurveyGranularity`). "detail" is exactly the
 * original single round trip; the two coarser bands tile the viewport into
 * `fetchDetailCollection` calls no larger than the same safe per-cell ceiling and
 * merge the results by drainage class, never erroring on a viewport too big to
 * cover in one go.
 * @param bbox "west,south,east,north"
 */
export async function getSoilSurvey(
  bbox: string,
  zoom?: number
): Promise<SoilSurveyCollection> {
  const granularity = resolveSoilSurveyGranularity(zoom);
  if (granularity === "detail") return fetchDetailCollection(bbox);
  return fetchAggregatedCollection(bbox, granularity);
}

/**
 * SSURGO map units clipped to the viewport, joined to their dominant component's
 * ratings. One round trip; see `src/lib/server/AGENTS.md` §soil-survey. The detail
 * tier's exact behavior, and also the per-cell fetch `fetchAggregatedCollection`
 * tiles — both trust this function's own ceiling-safety, so it is never handed a
 * bbox larger than `MAX_SOIL_BBOX_SQUARE_DEGREES` by either caller.
 */
async function fetchDetailCollection(bbox: string): Promise<SoilSurveyCollection> {
  const cacheKey = `usda-soil:${bbox}`;
  const cached = await getCachedGeoJSON<unknown>(cacheKey);
  if (isSoilSurveyCollection(cached)) return cached;

  const payload = await fetchBoundedJson(
    SDA_TABULAR_ENDPOINT,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        format: "JSON+COLUMNNAME",
        query: buildClippedMapunitQuery(bbox),
      }),
    },
    // No `revalidateSeconds`: Next.js does not cache a POST, so declaring a lifetime
    // here would claim a cache that does not exist. Redis above is the cache.
    { maxBytes: MAX_RESPONSE_BYTES, timeoutMs: REQUEST_TIMEOUT_MS }
  );

  const collection = toSoilSurveyCollection(payload);
  await cacheGeoJSON(cacheKey, collection, USDA_CACHE_TTL);
  return collection;
}

/**
 * Runs `fn` over `items` with at most `limit` in flight, preserving input order.
 * SDA is a shared, rate-sensitive upstream (§soil-survey measures its own per-call
 * cost) — an aggregated request must not fan out unboundedly just because the
 * viewport grew.
 */
async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<R>
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let nextIndex = 0;
  async function worker(): Promise<void> {
    for (;;) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= items.length) return;
      results[index] = await fn(items[index]);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(Math.max(limit, 1), items.length) }, worker)
  );
  return results;
}

/**
 * Tiles the viewport into a bounded grid of safe-sized cells, fetches each through
 * the exact same `fetchDetailCollection` path detail requests use (same cost, same
 * cache, same SDA query — nothing about the upstream ask changes), and merges the
 * real map units returned into one shape per drainage class per cell.
 *
 * A viewport whose grid would need more cells than the budget allows is centered on
 * rather than fetched from a corner — the sampled area is the part most likely under
 * the pointer — and the response comes back `truncated: true` instead of erroring.
 */
async function fetchAggregatedCollection(
  bbox: string,
  granularity: "regional-average" | "coarse-average"
): Promise<SoilSurveyCollection> {
  const cacheKey = `usda-soil-agg:${granularity}:${bbox}`;
  const cached = await getCachedGeoJSON<unknown>(cacheKey);
  if (isSoilSurveyCollection(cached)) return cached;

  const [west, south, east, north] = parseBbox(bbox);
  const cellSide = Math.sqrt(MAX_SOIL_BBOX_SQUARE_DEGREES);
  const wantedCols = Math.max(1, Math.ceil((east - west) / cellSide));
  const wantedRows = Math.max(1, Math.ceil((north - south) / cellSide));
  const cols = Math.min(wantedCols, MAX_SOIL_AGGREGATION_CELLS_PER_SIDE);
  const rows = Math.min(wantedRows, MAX_SOIL_AGGREGATION_CELLS_PER_SIDE);
  const boundedByBudget = cols < wantedCols || rows < wantedRows;

  const centerLongitude = (west + east) / 2;
  const centerLatitude = (south + north) / 2;
  const gridWest = centerLongitude - (cols * cellSide) / 2;
  const gridSouth = centerLatitude - (rows * cellSide) / 2;

  const cellBboxes: string[] = [];
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const cellWest = gridWest + col * cellSide;
      const cellSouth = gridSouth + row * cellSide;
      cellBboxes.push(
        [cellWest, cellSouth, cellWest + cellSide, cellSouth + cellSide]
          .map((value) => value.toFixed(6))
          .join(",")
      );
    }
  }

  const cellCollections = await mapWithConcurrency(
    cellBboxes,
    AGGREGATION_CONCURRENCY,
    fetchDetailCollection
  );

  const tolerance =
    granularity === "regional-average"
      ? REGIONAL_SIMPLIFY_TOLERANCE_DEGREES
      : COARSE_SIMPLIFY_TOLERANCE_DEGREES;
  const merged = await mergeDrainageClassGroups(cellCollections, tolerance);

  const collection: SoilSurveyCollection = {
    type: "FeatureCollection",
    features: merged.features,
    truncated: boundedByBudget || merged.truncated,
    unreadableGeometries: merged.unreadableGeometries,
    granularity,
  };
  await cacheGeoJSON(cacheKey, collection, USDA_CACHE_TTL);
  return collection;
}

/**
 * Merges each fetched cell's real map units by drainage class, using PostGIS as a
 * scratch geometry processor over data already in hand — nothing is written to a
 * table. `ST_Union` + `ST_SimplifyPreserveTopology` (zoom-derived `toleranceDegrees`)
 * produce one generalized shape per class per cell, tagged with the real map-unit
 * count behind it so the average is never mistaken for a surveyed unit.
 *
 * `toleranceDegrees` lands directly as `ST_SimplifyPreserveTopology`'s own typed
 * `double precision` argument, so it needs no `::numeric` cast — see the postgres-js
 * bigint-trap note on `OBSERVATION_DENSITY_FLOOR_FRACTION` in
 * `environmental-read-model.ts` for the case that *does* need one (a fractional
 * parameter multiplied against a `COUNT(*)` bigint in the same expression, which
 * nothing here does).
 */
async function mergeDrainageClassGroups(
  cellCollections: SoilSurveyCollection[],
  toleranceDegrees: number
): Promise<{
  features: GeoJSON.Feature[];
  unreadableGeometries: number;
  truncated: boolean;
}> {
  let unreadableGeometries = 0;
  let truncated = false;
  const inputRows: { drainageClass: string; hydric: boolean | null; geometry: GeoJSON.Geometry }[] =
    [];
  for (const cell of cellCollections) {
    unreadableGeometries += cell.unreadableGeometries;
    truncated = truncated || cell.truncated;
    for (const feature of cell.features) {
      if (!feature.geometry) continue;
      const properties = feature.properties as SoilSurveyProperties | null;
      inputRows.push({
        drainageClass: properties?.drainageClass ?? "unknown",
        hydric: properties?.hydric ?? null,
        geometry: feature.geometry,
      });
    }
  }

  if (inputRows.length === 0) {
    return { features: [], unreadableGeometries, truncated };
  }

  const payload = JSON.stringify(inputRows);
  const rows = await db.execute<{
    drainage_class: string;
    geometry: string | null;
    map_unit_count: string;
    hydric_count: string;
    rated_count: string;
  }>(sql`
    SELECT
      drainage_class,
      ST_AsGeoJSON(
        ST_SimplifyPreserveTopology(
          ST_CollectionExtract(ST_MakeValid(ST_Union(geom)), 3),
          ${toleranceDegrees}
        )
      ) AS geometry,
      COUNT(*) AS map_unit_count,
      COUNT(*) FILTER (WHERE hydric) AS hydric_count,
      COUNT(*) FILTER (WHERE hydric IS NOT NULL) AS rated_count
    FROM (
      SELECT
        elem->>'drainageClass' AS drainage_class,
        (elem->>'hydric')::boolean AS hydric,
        ST_SetSRID(ST_GeomFromGeoJSON((elem->'geometry')::text), 4326) AS geom
      FROM jsonb_array_elements(${payload}::jsonb) AS elem
    ) AS input
    GROUP BY drainage_class
  `);

  const features: GeoJSON.Feature[] = [];
  for (const row of rows) {
    // A degenerate union that left no polygon-typed result (e.g. every input row in
    // the class was a sliver ST_MakeValid collapsed to empty) is skipped rather than
    // emitted as a feature with no geometry.
    if (!row.geometry) continue;
    const mapUnitCount = Number(row.map_unit_count);
    const ratedCount = Number(row.rated_count);
    const hydricCount = Number(row.hydric_count);
    const properties: AggregatedSoilSurveyProperties = {
      aggregated: true,
      drainageClass: row.drainage_class,
      mapUnitCount,
      hydricFraction: ratedCount > 0 ? hydricCount / ratedCount : null,
    };
    features.push({
      type: "Feature",
      geometry: JSON.parse(row.geometry) as GeoJSON.Geometry,
      properties,
    });
  }

  return { features, unreadableGeometries, truncated };
}

/**
 * Builds the Soil Data Access spatial query. The `~Declare…~`/`~GetClippedMapunits~`
 * macros are SDA's own preprocessor, not T-SQL.
 */
function buildClippedMapunitQuery(bbox: string): string {
  const [west, south, east, north] = parseBbox(bbox);
  const aoi =
    `POLYGON((${west} ${south}, ${east} ${south}, ` +
    `${east} ${north}, ${west} ${north}, ${west} ${south}))`;

  return [
    "~DeclareGeometry(@aoi)~",
    `select @aoi = geometry::STPolyFromText('${aoi}', 4326)`,
    "~DeclareIdGeomTable(@clipped)~",
    "~GetClippedMapunits(@aoi,polygon,geo,@clipped)~",
    `select top ${MAX_SOIL_POLYGONS + 1}`,
    `  g.id as ${MUKEY_COLUMN}, mu.muname, c.compname, c.drainagecl,`,
    `  c.hydricrating, c.nirrcapcl, g.geom as ${GEOMETRY_COLUMN}`,
    "from @clipped g",
    "inner join mapunit mu on mu.mukey = g.id",
    "left join component c on c.mukey = mu.mukey",
    "  and c.cokey = (select top 1 c2.cokey from component c2",
    "    where c2.mukey = mu.mukey order by c2.comppct_r desc, c2.cokey)",
    // Stable order so the row the TOP ceiling drops is the same on every call.
    "order by g.id",
  ].join("\n");
}

/**
 * Rejects anything that is not four finite WGS84 numbers. The router validates too,
 * but these values are interpolated into SQL, so the service refuses to trust them.
 * A `RangeError` rather than a `SoilSurveyResponseError`: a bad bbox is our bug, and
 * reporting it as an upstream fault would blame SDA for a request we never sent.
 */
function parseBbox(bbox: string): [number, number, number, number] {
  const parts = bbox.split(",").map(Number);
  const isLegal =
    parts.length === 4 &&
    parts.every(Number.isFinite) &&
    parts[0] >= -180 &&
    parts[2] <= 180 &&
    parts[1] >= -90 &&
    parts[3] <= 90 &&
    parts[0] < parts[2] &&
    parts[1] < parts[3];
  if (!isLegal) {
    throw new RangeError('bbox must be "west,south,east,north" within WGS84 bounds');
  }
  return [parts[0], parts[1], parts[2], parts[3]];
}

/**
 * SDA returns `{"Table": [[columnName, …], [cell, …], …]}`, and a bare `{}` where it
 * holds no map units — an honest coverage gap, not a fault.
 */
function toSoilSurveyCollection(payload: unknown): SoilSurveyCollection {
  if (typeof payload !== "object" || payload === null) {
    throw new SoilSurveyResponseError("Soil Data Access returned a non-object body");
  }
  const table = (payload as { Table?: unknown }).Table;
  if (table === undefined) {
    return {
      type: "FeatureCollection",
      features: [],
      truncated: false,
      unreadableGeometries: 0,
      granularity: "detail",
    };
  }
  if (!Array.isArray(table) || table.length === 0 || !Array.isArray(table[0])) {
    throw new SoilSurveyResponseError("Soil Data Access returned no result table");
  }

  const columnIndex = new Map<string, number>(
    (table[0] as unknown[]).map((name, index) => [String(name), index])
  );
  const cellAt = (row: unknown[], column: string): unknown => {
    const index = columnIndex.get(column);
    return index === undefined ? null : row[index];
  };

  const rows = table.slice(1);
  const truncated = rows.length > MAX_SOIL_POLYGONS;
  const features: GeoJSON.Feature[] = [];
  let unreadableGeometries = 0;
  for (const row of rows.slice(0, MAX_SOIL_POLYGONS)) {
    if (!Array.isArray(row)) {
      throw new SoilSurveyResponseError("Soil Data Access returned a malformed row");
    }
    const geometry = parseWktPolygon(cellAt(row, GEOMETRY_COLUMN));
    // A map unit whose geometry will not parse is dropped rather than drawn at a
    // guessed outline; the ratings alone cannot be placed on the map. Counted, because
    // a silent drop is indistinguishable downstream from ground SSURGO never surveyed.
    if (geometry === null) {
      unreadableGeometries += 1;
      continue;
    }
    const properties: SoilSurveyProperties = {
      mukey: String(cellAt(row, MUKEY_COLUMN) ?? ""),
      muname: textOrNull(cellAt(row, "muname")),
      soilSeries: textOrNull(cellAt(row, "compname")),
      drainageClass: normalizeDrainageClass(textOrNull(cellAt(row, "drainagecl"))),
      hydric: parseHydricRating(cellAt(row, "hydricrating")),
      landCapabilityClass: textOrNull(cellAt(row, "nirrcapcl")),
    };
    features.push({ type: "Feature", geometry, properties });
  }

  return {
    type: "FeatureCollection",
    features,
    truncated,
    unreadableGeometries,
    granularity: "detail",
  };
}

/**
 * Guard for a cached payload, which is untrusted like any other external value.
 * `unreadableGeometries` and `granularity` are both required, so an entry written
 * before either field existed is re-fetched rather than served: it cannot say how
 * many rows it dropped or which tier it was built for, and defaulting either would
 * assert something nobody measured.
 */
function isSoilSurveyCollection(value: unknown): value is SoilSurveyCollection {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as {
    features?: unknown;
    truncated?: unknown;
    unreadableGeometries?: unknown;
    granularity?: unknown;
  };
  return (
    Array.isArray(candidate.features) &&
    typeof candidate.truncated === "boolean" &&
    typeof candidate.unreadableGeometries === "number" &&
    typeof candidate.granularity === "string"
  );
}

/** Non-blank text, or null — SSURGO leaves ratings null on components it never rated. */
function textOrNull(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return value.trim() || null;
}

/**
 * SSURGO rates hydric soils "Yes"/"No" and marks the rest "Unranked". Only a real
 * rating becomes a boolean: `Boolean("No")` is `true`, and an absent rating is not a
 * "No", so both would render a hydric verdict SSURGO never issued.
 */
function parseHydricRating(value: unknown): boolean | null {
  if (typeof value !== "string") return null;
  const rating = value.trim().toLowerCase();
  if (rating === "yes") return true;
  if (rating === "no") return false;
  return null;
}

/**
 * Maps an SSURGO `drainagecl` string onto one of our stable class ids.
 *
 * Order is load-bearing: every test is a substring match, so the more specific
 * phrase must be tried first. "Moderately well drained" contains "well" and "very
 * poorly drained" contains "poorly", so the previous ordering made those two classes
 * unreachable -- SSURGO's own labels for them were silently downgraded to
 * "well-drained" and "poorly-drained", which reports better drainage than the survey
 * recorded. Keep specific before general when adding a class.
 */
function normalizeDrainageClass(raw: string | null): string | null {
  if (raw === null) return null;
  const lower = raw.toLowerCase();
  if (lower.includes("very poorly")) return "very-poorly-drained";
  if (lower.includes("somewhat poorly")) return "somewhat-poorly-drained";
  if (lower.includes("poorly")) return "poorly-drained";
  if (lower.includes("moderately well")) return "moderately-well-drained";
  if (lower.includes("somewhat excessively")) return "somewhat-excessively-drained";
  if (lower.includes("excessively")) return "excessively-drained";
  if (lower.includes("well")) return "well-drained";
  return raw;
}

/** Splits on commas that sit outside every parenthesis. */
function splitTopLevel(text: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (character === "(") depth += 1;
    else if (character === ")") depth -= 1;
    else if (character === "," && depth === 0) {
      parts.push(text.slice(start, index));
      start = index + 1;
    }
  }
  parts.push(text.slice(start));
  return parts;
}

/** Strips one balanced outer parenthesis pair; null when the text is not so wrapped. */
function unwrapParentheses(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith("(") || !trimmed.endsWith(")")) return null;
  return trimmed.slice(1, -1);
}

/** Parses "lon lat, lon lat, …" into GeoJSON positions; null unless it closes a ring. */
function parseLinearRing(text: string): GeoJSON.Position[] | null {
  const positions: GeoJSON.Position[] = [];
  for (const pair of text.split(",")) {
    const [longitudeText, latitudeText] = pair.trim().split(/\s+/);
    const longitude = Number(longitudeText);
    const latitude = Number(latitudeText);
    if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return null;
    positions.push([longitude, latitude]);
  }
  if (positions.length < 4) return null;
  // RFC 7946 3.1.6: the last position must repeat the first. Emitting an unclosed ring
  // would hand the renderer a shape to auto-close into some polygon the survey never
  // recorded, which is the repaired geometry parseWktPolygon promises never to produce.
  const first = positions[0];
  const last = positions[positions.length - 1];
  return first[0] === last[0] && first[1] === last[1] ? positions : null;
}

/** Parses an exterior ring followed by any interior rings. */
function parsePolygonRings(body: string): GeoJSON.Position[][] | null {
  const rings: GeoJSON.Position[][] = [];
  for (const ringText of splitTopLevel(body)) {
    const inner = unwrapParentheses(ringText);
    if (inner === null) return null;
    const ring = parseLinearRing(inner);
    if (ring === null) return null;
    rings.push(ring);
  }
  return rings.length > 0 ? rings : null;
}

/**
 * Parses SQL Server `STAsText()` output. Null for EMPTY, a non-areal type, or text
 * this parser cannot read — never a partial or repaired geometry.
 */
function parseWktPolygon(value: unknown): GeoJSON.Polygon | GeoJSON.MultiPolygon | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  const openIndex = trimmed.indexOf("(");
  if (openIndex < 0) return null;
  const keyword = trimmed.slice(0, openIndex).trim().toUpperCase();
  const body = unwrapParentheses(trimmed.slice(openIndex));
  if (body === null) return null;

  if (keyword === "POLYGON") {
    const coordinates = parsePolygonRings(body);
    return coordinates === null ? null : { type: "Polygon", coordinates };
  }
  if (keyword === "MULTIPOLYGON") {
    const coordinates: GeoJSON.Position[][][] = [];
    for (const polygonText of splitTopLevel(body)) {
      const inner = unwrapParentheses(polygonText);
      if (inner === null) return null;
      const rings = parsePolygonRings(inner);
      if (rings === null) return null;
      coordinates.push(rings);
    }
    return coordinates.length > 0 ? { type: "MultiPolygon", coordinates } : null;
  }
  return null;
}
