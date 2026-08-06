import { randomUUID } from "node:crypto";
import { sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { fetchBoundedJson } from "@/lib/server/http/bounded-upstream";
import {
  resolveZoomGranularity,
  type ZoomGranularity,
  type ZoomGranularityTiers,
} from "@/lib/server/services/zoom-granularity";

/**
 * USDA Soil Data Access tabular SQL endpoint. Geometry and ratings both come from
 * here; see `src/lib/server/AGENTS.md` §soil-survey for why the SSURGO WFS cannot.
 */
const SDA_TABULAR_ENDPOINT =
  "https://SDMDataAccess.nrcs.usda.gov/Tabular/post.rest";

/**
 * Measured 2026-08-05 against the live endpoint: one 1/8-degree cell over the Boise
 * foothills served 649 delineations in 2.78 MB, and a cell five times that area served
 * 2,586 in 11.6 MB — ~4.4 KB of WKT per delineation. The 8 MB bound this module carried
 * while it fetched *clipped* map units is therefore too small for whole delineations at
 * the ingest ceiling below (4,000 x 4.4 KB ~ 18 MB).
 */
const MAX_RESPONSE_BYTES = 24 * 1024 * 1024;

/** Measured 4.3 s for one cell; the bound clears a dense cell with headroom. */
const REQUEST_TIMEOUT_MS = 30_000;

/** Map-unit polygons served per viewport; one more is read so truncation is detectable. */
export const MAX_SOIL_POLYGONS = 1000;

/**
 * Delineations fetched per grid cell during ingest. Larger than the render ceiling on
 * purpose: the render ceiling bounds what one viewport draws, while this bounds what one
 * cell *stores*, and a cell truncated at ingest is permanently incomplete. Measured
 * ceiling headroom: the densest cell probed served 649.
 */
export const MAX_SOIL_INGEST_POLYGONS_PER_CELL = 4000;

/**
 * Viewport ceiling in square degrees for the detail tier, measured rather than guessed —
 * see `src/lib/server/AGENTS.md` §soil-survey. Since persistence landed it bounds the
 * read-through ingest a first visit pays, not the read: a 0.02 sq deg viewport spans at
 * most 2x2 cells of `SOIL_SURVEY_CELL_DEGREES`, and every later visit to the same ground
 * is a local PostGIS query.
 */
export const MAX_SOIL_BBOX_SQUARE_DEGREES = 0.02;

/**
 * The persistence grid, in degrees per side. 1/8 exactly, because it is exactly
 * representable in binary floating point: cell indices are then `floor(lon / 0.125)`
 * with no drift, so the same ground always resolves to the same `cell_key` and the
 * coverage ledger cannot develop near-duplicate rows. Area 0.015625 sq deg, just inside
 * `MAX_SOIL_BBOX_SQUARE_DEGREES` — the largest area one SDA round trip is measured safe
 * over — so no cell fetch ever asks SDA for more than that.
 */
export const SOIL_SURVEY_CELL_DEGREES = 0.125;

/** The `geo.layers` row persisted map units hang off (`drizzle/0013`). */
export const SOIL_SURVEY_LAYER_NAME = "soil-survey";

/**
 * Producer token for `geo.geometry.natural_key`, which is always
 * `'<producer>:<producer-local id>'`. No `_` and no `%`, as
 * `ck_geometry_natural_key_namespaced` and `identity.PRODUCER_TOKEN_PATTERN` require.
 */
export const SOIL_SURVEY_PRODUCER = "usda-sda";

export interface SoilSurveyProperties {
  mukey: string;
  muname: string | null;
  soilSeries: string | null;
  drainageClass: string | null;
  /** Tri-state: SSURGO rates a component Yes or No, or leaves it unranked. */
  hydric: boolean | null;
  landCapabilityClass: string | null;
  /** SSURGO survey area (`legend.areasymbol`), e.g. "ID001". */
  areaSymbol: string | null;
  /**
   * The survey area's own vintage as an ISO date (`sacatalog.saverest`), which is the
   * only publication time SSURGO issues for a map unit. Not an observation date: see
   * §soil-survey-persistence on why the collection's `observedAt` stays null.
   */
  surveyAreaVintage: string | null;
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
 *
 * An alias for the shared vocabulary in `zoom-granularity.ts` rather than its own union:
 * the soil-moisture field reuses the same three tier names, and two independent unions
 * would let a panel's wording for one drift from the other.
 */
export type SoilSurveyGranularity = ZoomGranularity;

/**
 * How much of the viewport the warehouse can actually answer for.
 *
 * The concept persistence forced into existence. A live proxy had one honest-empty
 * case — SDA saying "nothing surveyed here" — while a warehouse read has two, and the
 * second (ground nobody has fetched) paints identically. `covered < cells` is the only
 * signal that tells them apart, so it is carried to the client rather than absorbed.
 */
export interface SoilSurveyCoverage {
  /** Grid cells the viewport overlaps, after any per-request cell budget. */
  cells: number;
  /** …of those, cells the ledger records a completed SDA fetch for. */
  covered: number;
  /** …of those, cells fetched from SDA during this request (read-through warming). */
  ingested: number;
}

export interface SoilSurveyCollection extends GeoJSON.FeatureCollection {
  /**
   * The view is a subset. Either a cell stored fewer delineations than SDA held for it,
   * or the viewport needed more cells than the per-request budget covers, or the read
   * itself hit its row ceiling.
   */
  truncated: boolean;
  /**
   * Rows SDA served for the cells in view that never became stored map units — geometry
   * this reader could not parse, or a survey area with no publisher vintage to date the
   * version by. Non-zero means the view is incomplete for a reason USDA never reported,
   * so it must not be presented as an absence of soil.
   */
  unreadableGeometries: number;
  granularity: SoilSurveyGranularity;
  coverage: SoilSurveyCoverage;
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

/** Where the SSURGO survey puts its two tier boundaries, in the shared vocabulary. */
export const SOIL_SURVEY_TIERS: ZoomGranularityTiers = {
  detailMinZoom: SOIL_SURVEY_DETAIL_MIN_ZOOM,
  regionalMinZoom: SOIL_SURVEY_REGIONAL_MIN_ZOOM,
};

/**
 * No zoom means a legacy caller — keep the original single-shot, hard-capped
 * behavior exactly as it was rather than guessing which tier it meant.
 */
export function resolveSoilSurveyGranularity(zoom?: number): SoilSurveyGranularity {
  return resolveZoomGranularity(zoom, SOIL_SURVEY_TIERS);
}

/**
 * The viewport-area ceiling the tRPC input schema enforces before this service ever
 * runs. A detail-tier request may warm at most a 2x2 patch of cells, so it keeps the
 * original measured ceiling. An aggregated request warms inside the same per-cell
 * ceiling and degrades to `truncated: true` instead of erroring when the viewport
 * outgrows its cell budget, so it carries no ceiling at the schema level at all.
 */
export function soilSurveyAreaCeiling(zoom?: number): number | null {
  return resolveSoilSurveyGranularity(zoom) === "detail" ? MAX_SOIL_BBOX_SQUARE_DEGREES : null;
}

/**
 * Cells one request may warm from SDA, per side of the grid. Bounds the upstream cost a
 * single unlucky pan can trigger: 3x3 covers 9x `SOIL_SURVEY_CELL_DEGREES` squared, and
 * a viewport needing more is answered from whatever the ledger already covers with
 * `coverage.covered < coverage.cells` saying so. Backfill is how coverage grows in bulk;
 * a page view is not.
 */
export const MAX_SOIL_AGGREGATION_CELLS_PER_SIDE = 3;

/** SDA fetches in flight at once, for read-through and backfill alike. */
const SDA_CONCURRENCY = 3;

/** Simplify tolerance (degrees) for the finer averaged band. */
const REGIONAL_SIMPLIFY_TOLERANCE_DEGREES = 0.0015;

/** Simplify tolerance (degrees) for the coarser averaged band. */
const COARSE_SIMPLIFY_TOLERANCE_DEGREES = 0.005;

/**
 * Delineations an averaged read may union. `ST_Union` over a whole state's SSURGO is
 * minutes of CPU, so the input is bounded and the answer reports itself truncated
 * rather than being allowed to run long.
 */
const MAX_SOIL_AGGREGATE_INPUT_ROWS = 20_000;

/** Soil Data Access answered, but not with a result table this module can read. */
export class SoilSurveyResponseError extends Error {}

/** Column aliases the query sets and the reader looks up by name. */
const POLYGON_KEY_COLUMN = "mupolygonkey";
const MUKEY_COLUMN = "mukey";
const GEOMETRY_COLUMN = "geom";

/** One SSURGO delineation, parsed and ready to persist. */
export interface PersistableMapUnit {
  /** `mupolygon.mupolygonkey` — SSURGO's own per-delineation primary key. */
  polygonKey: string;
  /** The survey area's vintage as an explicit UTC instant, for `version_valid_from`. */
  vintageInstant: string;
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
  properties: SoilSurveyProperties;
}

export interface ParsedSoilSurveyRows {
  units: PersistableMapUnit[];
  /** SDA held more delineations for the cell than the ingest ceiling served. */
  truncated: boolean;
  /** Rows served that had no readable geometry, or no survey-area vintage. */
  unreadable: number;
}

/** A grid cell, identified and bounded. */
export interface SoilSurveyCell {
  key: string;
  west: number;
  south: number;
  east: number;
  north: number;
}

/**
 * SSURGO map units for the viewport, at the granularity `zoom` resolves to (see
 * §soil-survey-zoom above `resolveSoilSurveyGranularity`).
 *
 * Read from the warehouse, not from USDA. Cells the coverage ledger has no row for are
 * warmed from SDA first, bounded by `MAX_SOIL_AGGREGATION_CELLS_PER_SIDE`; everything
 * else is one local PostGIS query. See `src/lib/server/AGENTS.md`
 * §soil-survey-persistence.
 * @param bbox "west,south,east,north"
 */
export async function getSoilSurvey(
  bbox: string,
  zoom?: number
): Promise<SoilSurveyCollection> {
  const bounds = parseBbox(bbox);
  const granularity = resolveSoilSurveyGranularity(zoom);
  const { cells, budgetExceeded } = soilSurveyCellsForBbox(bounds);

  const coverageBefore = await readCoverage(cells);
  const missing = cells.filter((cell) => !coverageBefore.has(cell.key));
  const warmed = await mapWithConcurrency(missing, SDA_CONCURRENCY, ingestSoilSurveyCell);
  const ingested = warmed.filter((result) => result !== null).length;

  const coverage = await readCoverage(cells);
  const summary: SoilSurveyCoverage = {
    cells: cells.length,
    covered: coverage.size,
    ingested,
  };
  let unreadableGeometries = 0;
  let ledgerTruncated = false;
  for (const row of coverage.values()) {
    unreadableGeometries += row.unreadableCount;
    ledgerTruncated = ledgerTruncated || row.truncated;
  }
  const partialCoverage = budgetExceeded || summary.covered < summary.cells;

  const read =
    granularity === "detail"
      ? await readDetailFeatures(bounds)
      : await readAggregatedFeatures(
          bounds,
          granularity === "regional-average"
            ? REGIONAL_SIMPLIFY_TOLERANCE_DEGREES
            : COARSE_SIMPLIFY_TOLERANCE_DEGREES
        );

  return {
    type: "FeatureCollection",
    features: read.features,
    truncated: read.truncated || ledgerTruncated || partialCoverage,
    unreadableGeometries,
    granularity,
    coverage: summary,
  };
}

/**
 * The cell a coordinate falls in. Exact because `SOIL_SURVEY_CELL_DEGREES` is 1/8: the
 * division is representable, so no coordinate ever lands in two cells or none.
 */
export function soilSurveyCellKey(longitude: number, latitude: number): string {
  const col = Math.floor(longitude / SOIL_SURVEY_CELL_DEGREES);
  const row = Math.floor(latitude / SOIL_SURVEY_CELL_DEGREES);
  return `${col}:${row}`;
}

/** The cell at grid indices `col`,`row`, with its bounds. */
function cellAt(col: number, row: number): SoilSurveyCell {
  return {
    key: `${col}:${row}`,
    west: col * SOIL_SURVEY_CELL_DEGREES,
    south: row * SOIL_SURVEY_CELL_DEGREES,
    east: (col + 1) * SOIL_SURVEY_CELL_DEGREES,
    north: (row + 1) * SOIL_SURVEY_CELL_DEGREES,
  };
}

/**
 * The grid cells a bbox overlaps, capped per side at
 * `MAX_SOIL_AGGREGATION_CELLS_PER_SIDE`. A viewport past the cap is *centered* on
 * rather than clipped from a corner — the sampled ground is the part most likely under
 * the pointer — and `budgetExceeded` says the answer describes part of the view.
 *
 * `Math.ceil(east / cell) - 1` rather than `Math.floor`, so a bbox whose east edge sits
 * exactly on a cell boundary does not claim the zero-width cell beyond it.
 */
export function soilSurveyCellsForBbox(
  bounds: [number, number, number, number],
  maxPerSide = MAX_SOIL_AGGREGATION_CELLS_PER_SIDE
): { cells: SoilSurveyCell[]; budgetExceeded: boolean } {
  const [west, south, east, north] = bounds;
  const colFrom = Math.floor(west / SOIL_SURVEY_CELL_DEGREES);
  const colTo = Math.ceil(east / SOIL_SURVEY_CELL_DEGREES) - 1;
  const rowFrom = Math.floor(south / SOIL_SURVEY_CELL_DEGREES);
  const rowTo = Math.ceil(north / SOIL_SURVEY_CELL_DEGREES) - 1;
  const wantedCols = Math.max(1, colTo - colFrom + 1);
  const wantedRows = Math.max(1, rowTo - rowFrom + 1);
  const cols = Math.min(wantedCols, maxPerSide);
  const rows = Math.min(wantedRows, maxPerSide);
  const budgetExceeded = cols < wantedCols || rows < wantedRows;

  // Centre the sampled window on the viewport when the budget bites.
  const startCol = colFrom + Math.floor((wantedCols - cols) / 2);
  const startRow = rowFrom + Math.floor((wantedRows - rows) / 2);

  const cells: SoilSurveyCell[] = [];
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      cells.push(cellAt(startCol + col, startRow + row));
    }
  }
  return { cells, budgetExceeded };
}

/**
 * Every cell of the grid covering an envelope, unbounded — the backfill's work list.
 * Separate from `soilSurveyCellsForBbox` because a page view must never enumerate this:
 * the PNW envelope `-125,42,-111,49` is 112 x 56 = 6,272 cells.
 */
export function soilSurveyCellsCovering(
  bounds: [number, number, number, number]
): SoilSurveyCell[] {
  const [west, south, east, north] = bounds;
  const colFrom = Math.floor(west / SOIL_SURVEY_CELL_DEGREES);
  const colTo = Math.ceil(east / SOIL_SURVEY_CELL_DEGREES) - 1;
  const rowFrom = Math.floor(south / SOIL_SURVEY_CELL_DEGREES);
  const rowTo = Math.ceil(north / SOIL_SURVEY_CELL_DEGREES) - 1;
  const cells: SoilSurveyCell[] = [];
  for (let row = rowFrom; row <= rowTo; row += 1) {
    for (let col = colFrom; col <= colTo; col += 1) {
      cells.push(cellAt(col, row));
    }
  }
  return cells;
}

interface CoverageRow {
  cellKey: string;
  polygonCount: number;
  unreadableCount: number;
  truncated: boolean;
}

/** The ledger rows for a set of cells, by key. Absent means never fetched. */
async function readCoverage(
  cells: SoilSurveyCell[]
): Promise<Map<string, CoverageRow>> {
  const found = new Map<string, CoverageRow>();
  if (cells.length === 0) return found;
  // The keys travel as one JSONB text parameter rather than a bound array: every other
  // multi-value statement in this codebase does the same (`ingest.ts` builds an
  // `ARRAY[...]` out of joined parameters), and postgres-js array binding is a type-
  // inference surface this read does not need to depend on.
  const keys = JSON.stringify(cells.map((cell) => cell.key));
  const rows = await db.execute<{
    cell_key: string;
    polygon_count: number | string;
    unreadable_count: number | string;
    truncated: boolean;
  }>(sql`
    SELECT cell_key, polygon_count, unreadable_count, truncated
    FROM geo.soil_survey_coverage
    WHERE cell_key IN (SELECT jsonb_array_elements_text(${keys}::jsonb))
  `);
  for (const row of rows) {
    found.set(row.cell_key, {
      cellKey: row.cell_key,
      polygonCount: Number(row.polygon_count),
      unreadableCount: Number(row.unreadable_count),
      truncated: row.truncated === true,
    });
  }
  return found;
}

/** The keys of the cells in `cells` the ledger already covers. */
export async function soilSurveyCoveredCellKeys(
  cells: SoilSurveyCell[]
): Promise<Set<string>> {
  return new Set((await readCoverage(cells)).keys());
}

/**
 * Fetches one grid cell from SDA and persists it, returning the ledger row written.
 * Null when SDA answered with nothing readable — the cell is left uncovered so a later
 * request retries it, rather than being recorded as surveyed-and-empty.
 *
 * Idempotent: re-running a cell confirms the versions it already stored instead of
 * opening new ones (SSURGO is a static product; see `persistCell`).
 */
export async function ingestSoilSurveyCell(
  cell: SoilSurveyCell
): Promise<CoverageRow | null> {
  // Faults propagate deliberately uncaught: a transport failure must not become a
  // coverage claim, so the cell is left unrecorded and a later read retries it.
  const payload = await fetchBoundedJson(
    SDA_TABULAR_ENDPOINT,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        format: "JSON+COLUMNNAME",
        query: buildPersistableMapunitQuery(cell),
      }),
    },
    // No `revalidateSeconds`: Next.js does not cache a POST. The warehouse is the cache
    // now, and it is a cache of record rather than a lifetime-bounded one.
    { maxBytes: MAX_RESPONSE_BYTES, timeoutMs: REQUEST_TIMEOUT_MS }
  );

  const parsed = parseSoilSurveyRows(payload);
  return persistCell(cell, parsed);
}

/**
 * Builds the Soil Data Access query for one cell.
 *
 * Reads `mupolygon` directly rather than through the `~GetClippedMapunits~` macro the
 * live-proxy path used, for two reasons measured on 2026-08-05. The macro *clips* to the
 * requested AOI, so the shape it returns depends on the viewport that asked — persisting
 * that would store an arbitrary clip of a real boundary and make blank ground appear on
 * the next pan. And the macro keys rows on `mukey`, which is not unique per shape: over
 * one Boise cell it returned 683 rows across only 98 distinct mukeys, so keying identity
 * on mukey would have collapsed 683 delineations into 98. `mupolygonkey` is SSURGO's own
 * per-delineation primary key and is what `geo.geometry.natural_key` is built from.
 *
 * `sacatalog.saverest` is the survey area's own publication vintage — the only
 * publication time SSURGO issues for a map unit, and what dates the stored version.
 * Verified over the PNW envelope: all 220 intersecting survey areas carry one, ranging
 * 2025-08-26 to 2026-03-19.
 *
 * The `~Declare…~` macro is SDA's own preprocessor, not T-SQL.
 */
function buildPersistableMapunitQuery(cell: SoilSurveyCell): string {
  const { west, south, east, north } = cell;
  const aoi =
    `POLYGON((${west} ${south}, ${east} ${south}, ` +
    `${east} ${north}, ${west} ${north}, ${west} ${south}))`;

  return [
    "~DeclareGeometry(@aoi)~",
    `select @aoi = geometry::STPolyFromText('${aoi}', 4326)`,
    `select top ${MAX_SOIL_INGEST_POLYGONS_PER_CELL + 1}`,
    `  p.${POLYGON_KEY_COLUMN}, p.${MUKEY_COLUMN}, mu.muname, lg.areasymbol, sac.saverest,`,
    "  c.compname, c.drainagecl, c.hydricrating, c.nirrcapcl,",
    `  p.mupolygongeo.STAsText() as ${GEOMETRY_COLUMN}`,
    "from mupolygon p",
    "inner join mapunit mu on mu.mukey = p.mukey",
    "inner join legend lg on lg.lkey = mu.lkey",
    "left join sacatalog sac on sac.areasymbol = lg.areasymbol",
    "left join component c on c.mukey = mu.mukey",
    "  and c.cokey = (select top 1 c2.cokey from component c2",
    "    where c2.mukey = mu.mukey order by c2.comppct_r desc, c2.cokey)",
    "where p.mupolygongeo.STIntersects(@aoi) = 1",
    // Stable order so the row the TOP ceiling drops is the same on every call.
    `order by p.${POLYGON_KEY_COLUMN}`,
  ].join("\n");
}

/**
 * Runs `fn` over `items` with at most `limit` in flight, preserving input order.
 * SDA is a shared, rate-sensitive upstream (§soil-survey measures its own per-call
 * cost) — neither a read-through nor a backfill may fan out unboundedly.
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
 * SDA returns `{"Table": [[columnName, …], [cell, …], …]}`, and a bare `{}` where it
 * holds no map units — an honest coverage gap, not a fault.
 *
 * Exported because it is the whole normalization contract: WKT to GeoJSON, SSURGO's
 * tri-state hydric rating, the drainage-class vocabulary, and the survey-area vintage.
 */
export function parseSoilSurveyRows(payload: unknown): ParsedSoilSurveyRows {
  if (typeof payload !== "object" || payload === null) {
    throw new SoilSurveyResponseError("Soil Data Access returned a non-object body");
  }
  const table = (payload as { Table?: unknown }).Table;
  if (table === undefined) {
    return { units: [], truncated: false, unreadable: 0 };
  }
  if (!Array.isArray(table) || table.length === 0 || !Array.isArray(table[0])) {
    throw new SoilSurveyResponseError("Soil Data Access returned no result table");
  }

  const columnIndex = new Map<string, number>(
    (table[0] as unknown[]).map((name, index) => [String(name), index])
  );
  const cellAtColumn = (row: unknown[], column: string): unknown => {
    const index = columnIndex.get(column);
    return index === undefined ? null : row[index];
  };

  const rows = table.slice(1);
  const truncated = rows.length > MAX_SOIL_INGEST_POLYGONS_PER_CELL;
  const units: PersistableMapUnit[] = [];
  const seen = new Set<string>();
  let unreadable = 0;
  for (const row of rows.slice(0, MAX_SOIL_INGEST_POLYGONS_PER_CELL)) {
    if (!Array.isArray(row)) {
      throw new SoilSurveyResponseError("Soil Data Access returned a malformed row");
    }
    const geometry = parseWktPolygon(cellAtColumn(row, GEOMETRY_COLUMN));
    const polygonKey = textOrNull(cellAtColumn(row, POLYGON_KEY_COLUMN));
    const vintage = parseSurveyAreaVintage(cellAtColumn(row, "saverest"));
    // Three ways a served row cannot become a stored map unit, all counted rather than
    // dropped silently: geometry that will not parse (drawing a guessed outline would
    // place a boundary the survey never recorded), no delineation key (nothing to key
    // identity on), and no survey-area vintage (nothing honest to date the version by,
    // and `geo.geometry.version_valid_from` is NOT NULL). A silent drop is
    // indistinguishable downstream from ground SSURGO never surveyed.
    if (geometry === null || polygonKey === null || vintage === null) {
      unreadable += 1;
      continue;
    }
    // SDA can repeat a delineation when a mapunit joins several components; the last
    // write would otherwise make `ON CONFLICT` fail with "cannot affect row a second
    // time". The component join is already narrowed to one row, so a repeat here is an
    // upstream anomaly and the first copy is kept.
    if (seen.has(polygonKey)) continue;
    seen.add(polygonKey);

    units.push({
      polygonKey,
      vintageInstant: vintage.instant,
      geometry,
      properties: {
        mukey: String(cellAtColumn(row, MUKEY_COLUMN) ?? ""),
        muname: textOrNull(cellAtColumn(row, "muname")),
        soilSeries: textOrNull(cellAtColumn(row, "compname")),
        drainageClass: normalizeDrainageClass(textOrNull(cellAtColumn(row, "drainagecl"))),
        hydric: parseHydricRating(cellAtColumn(row, "hydricrating")),
        landCapabilityClass: textOrNull(cellAtColumn(row, "nirrcapcl")),
        areaSymbol: textOrNull(cellAtColumn(row, "areasymbol")),
        surveyAreaVintage: vintage.date,
      },
    });
  }

  return { units, truncated, unreadable };
}

/**
 * Parses `sacatalog.saverest`, which SDA serves as US-locale text
 * ("8/27/2025 8:27:08 PM") with no timezone at all.
 *
 * The clock time is deliberately discarded and the date is taken as UTC midnight.
 * Keeping the time would look more faithful while asserting a timezone the publisher
 * never stated; a date is what SSURGO's own release cadence is expressed in anyway.
 * Returns null rather than a guess for anything that does not match, which makes the
 * row unstorable and counted — see `parseSoilSurveyRows`.
 */
function parseSurveyAreaVintage(
  value: unknown
): { date: string; instant: string } | null {
  if (typeof value !== "string") return null;
  const match = /^\s*(\d{1,2})\/(\d{1,2})\/(\d{4})\b/.exec(value);
  if (!match) return null;
  const month = Number(match[1]);
  const day = Number(match[2]);
  const year = Number(match[3]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  const date = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  // Round-tripped so an impossible calendar date (2/30) is rejected rather than rolled.
  const instant = new Date(`${date}T00:00:00.000Z`);
  if (Number.isNaN(instant.getTime())) return null;
  if (instant.toISOString().slice(0, 10) !== date) return null;
  return { date, instant: instant.toISOString() };
}

/**
 * Writes one cell's delineations into `geo.geometry` + `geo.features` and records the
 * ledger row, all in one transaction.
 *
 * The version rule follows `src/lib/server/db/AGENTS.md` §geometry-dimension: version on
 * the producer's own revision signal, which for SSURGO is the survey area's `saverest`.
 * So a re-fetch of unchanged ground only advances `last_confirmed_at`, and a survey area
 * republished with a strictly newer vintage closes the old version and opens a new one.
 * That ordering is the only legal one — close v1 naming its successor, then insert the
 * successor, with the deferred self-FK validated at COMMIT.
 *
 * `polygon_count` is counted back out of `geo.features` after the writes rather than
 * taken from the parse, and `unreadable_count` is served-minus-stored. Any row lost
 * anywhere in this path — including inside PostGIS, where this code cannot see it — is
 * therefore visible to the reader instead of quietly shrinking the map.
 */
async function persistCell(
  cell: SoilSurveyCell,
  parsed: ParsedSoilSurveyRows
): Promise<CoverageRow> {
  const payload = JSON.stringify(
    parsed.units.map((unit) => ({
      polygonKey: unit.polygonKey,
      naturalKey: `${SOIL_SURVEY_PRODUCER}:${unit.polygonKey}`,
      vintage: unit.vintageInstant,
      geometry: unit.geometry,
      // Both ids are minted here, before any statement runs: closing a version has to
      // name its successor in the same statement that closes it, so the successor's id
      // cannot come from the insert that creates it.
      geometryId: randomUUID(),
      successorGeometryId: randomUUID(),
      properties: { ...unit.properties, id: unit.polygonKey, geometry: unit.geometry },
    }))
  );
  const servedRows = parsed.units.length + parsed.unreadable;

  return db.transaction(async (tx) => {
    // Two requests warming the same cell would otherwise both pass the "does this
    // feature exist" test and both insert. Same guard `ingest.ts` uses.
    await tx.execute(
      sql`SELECT pg_advisory_xact_lock(hashtext(${`soil-survey:${cell.key}`}))`
    );

    if (parsed.units.length > 0) {
      // 1. Open a version for every delineation that has none. A delineation already
      //    stored at this vintage is merely confirmed: the DO UPDATE's WHERE makes a
      //    vintage mismatch a no-op here, and step 2 handles it instead.
      // MATERIALIZED, not left to the planner: without it the CTE is inlined and
      // `ST_GeomFromGeoJSON` is evaluated once per column that references it — the
      // dominant cost of a 500-row cell. The centroid is taken from the *validated*
      // geometry, not the raw one, so the two columns can never describe different shapes.
      await tx.execute(sql`
        WITH parsed AS MATERIALIZED (
          SELECT
            (elem->>'geometryId')::uuid AS geometry_id,
            elem->>'naturalKey' AS natural_key,
            (elem->>'vintage')::timestamptz AS vintage,
            ST_MakeValid(
              ST_SetSRID(ST_GeomFromGeoJSON((elem->'geometry')::text), 4326)
            ) AS geom
          FROM jsonb_array_elements(${payload}::jsonb) AS elem
        )
        INSERT INTO geo.geometry AS g (
          geometry_id, natural_key, version_valid_from, geom_kind, geom, centroid, producer
        )
        SELECT
          p.geometry_id, p.natural_key, p.vintage, 'polygon',
          p.geom, ST_Centroid(p.geom), ${SOIL_SURVEY_PRODUCER}
        FROM parsed p
        ON CONFLICT (natural_key) WHERE version_valid_to IS NULL
        DO UPDATE SET last_confirmed_at = now()
        WHERE g.version_valid_from = excluded.version_valid_from
      `);

      // 2. Close versions whose survey area has been republished. Strictly-newer only:
      //    ck_geometry_version_order requires version_valid_to > version_valid_from, and
      //    a regressed upstream vintage is an anomaly to fail loudly on, not to absorb.
      await tx.execute(sql`
        UPDATE geo.geometry g
        SET version_valid_to = incoming.vintage, superseded_by = incoming.successor_id
        FROM (
          SELECT
            elem->>'naturalKey' AS natural_key,
            (elem->>'vintage')::timestamptz AS vintage,
            (elem->>'successorGeometryId')::uuid AS successor_id
          FROM jsonb_array_elements(${payload}::jsonb) AS elem
        ) AS incoming
        WHERE g.natural_key = incoming.natural_key
          AND g.version_valid_to IS NULL
          AND g.version_valid_from < incoming.vintage
      `);

      // 3. Insert the successors step 2 named. Selected by the closed row pointing at
      //    them, so nothing is inserted for a delineation that was not superseded.
      await tx.execute(sql`
        WITH superseded AS MATERIALIZED (
          SELECT
            (elem->>'successorGeometryId')::uuid AS geometry_id,
            elem->>'naturalKey' AS natural_key,
            (elem->>'vintage')::timestamptz AS vintage,
            ST_MakeValid(
              ST_SetSRID(ST_GeomFromGeoJSON((elem->'geometry')::text), 4326)
            ) AS geom
          FROM jsonb_array_elements(${payload}::jsonb) AS elem
          WHERE EXISTS (
            SELECT 1 FROM geo.geometry closed
            WHERE closed.superseded_by = (elem->>'successorGeometryId')::uuid
          )
        )
        INSERT INTO geo.geometry (
          geometry_id, natural_key, version_valid_from, geom_kind, geom, centroid, producer
        )
        SELECT
          s.geometry_id, s.natural_key, s.vintage, 'polygon',
          s.geom, ST_Centroid(s.geom), ${SOIL_SURVEY_PRODUCER}
        FROM superseded s
      `);

      // 4a. Refresh the features that already exist, repointing them at the current
      //     version. `properties` is written whole, so the drizzle/0004 trigger
      //     recomputes geom from properties.geometry.
      await tx.execute(sql`
        UPDATE geo.features f
        SET properties = incoming.properties,
            geometry_id = incoming.geometry_id,
            status = 'published',
            updated_at = now()
        FROM (
          SELECT
            elem->>'polygonKey' AS polygon_key,
            elem->'properties' AS properties,
            g.geometry_id
          FROM jsonb_array_elements(${payload}::jsonb) AS elem
          JOIN geo.geometry g
            ON g.natural_key = elem->>'naturalKey'
           AND g.version_valid_to IS NULL
        ) AS incoming
        WHERE f.layer_id = (SELECT id FROM geo.layers WHERE name = ${SOIL_SURVEY_LAYER_NAME})
          AND f.properties->>'id' = incoming.polygon_key
      `);

      // 4b. Insert the rest. NOT EXISTS rather than ON CONFLICT: the uniqueness guard is
      //     a partial expression index, and matching its inference clause exactly is a
      //     silent-failure hazard this does not need to take on.
      await tx.execute(sql`
        INSERT INTO geo.features (layer_id, properties, status, geometry_id)
        SELECT l.id, elem->'properties', 'published', g.geometry_id
        FROM jsonb_array_elements(${payload}::jsonb) AS elem
        JOIN geo.layers l ON l.name = ${SOIL_SURVEY_LAYER_NAME}
        JOIN geo.geometry g
          ON g.natural_key = elem->>'naturalKey'
         AND g.version_valid_to IS NULL
        WHERE NOT EXISTS (
          SELECT 1 FROM geo.features f
          WHERE f.layer_id = l.id
            AND f.properties->>'id' = elem->>'polygonKey'
        )
      `);
    }

    const [stored] = await tx.execute<{ stored: string }>(sql`
      SELECT count(*)::text AS stored
      FROM geo.features f
      JOIN geo.layers l ON l.id = f.layer_id
      WHERE l.name = ${SOIL_SURVEY_LAYER_NAME}
        AND f.status = 'published'
        AND f.properties->>'id' IN (
          SELECT elem->>'polygonKey' FROM jsonb_array_elements(${payload}::jsonb) AS elem
        )
    `);
    const polygonCount = Number(stored?.stored ?? 0);
    const unreadableCount = Math.max(servedRows - polygonCount, 0);

    await tx.execute(sql`
      INSERT INTO geo.soil_survey_coverage AS c (
        cell_key, west, south, east, north,
        polygon_count, unreadable_count, truncated, fetched_at
      )
      VALUES (
        ${cell.key},
        ${cell.west}::double precision,
        ${cell.south}::double precision,
        ${cell.east}::double precision,
        ${cell.north}::double precision,
        ${polygonCount}::integer,
        ${unreadableCount}::integer,
        ${parsed.truncated},
        now()
      )
      ON CONFLICT (cell_key) DO UPDATE SET
        polygon_count = excluded.polygon_count,
        unreadable_count = excluded.unreadable_count,
        truncated = excluded.truncated,
        fetched_at = excluded.fetched_at
    `);

    return {
      cellKey: cell.key,
      polygonCount,
      unreadableCount,
      truncated: parsed.truncated,
    };
  });
}

/**
 * Real per-map-unit delineations intersecting the viewport, read locally.
 *
 * Whole delineations, not clipped to the viewport: the renderer clips, and every other
 * polygon layer here serves whole shapes. The live-proxy path served clipped ones only
 * because SDA's macro clipped them.
 */
async function readDetailFeatures(
  bounds: [number, number, number, number]
): Promise<{ features: GeoJSON.Feature[]; truncated: boolean }> {
  const [west, south, east, north] = bounds;
  const rows = await db.execute<{
    polygon_key: string | null;
    geometry: string | null;
    properties: Record<string, unknown> | null;
  }>(sql`
    SELECT
      f.properties->>'id' AS polygon_key,
      ST_AsGeoJSON(f.geom) AS geometry,
      f.properties AS properties
    FROM geo.features f
    JOIN geo.layers l ON l.id = f.layer_id
    WHERE l.name = ${SOIL_SURVEY_LAYER_NAME}
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)
      AND ST_Intersects(f.geom, ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326))
    ORDER BY f.properties->>'id'
    LIMIT ${MAX_SOIL_POLYGONS + 1}
  `);

  const features: GeoJSON.Feature[] = [];
  for (const row of rows.slice(0, MAX_SOIL_POLYGONS)) {
    if (!row.geometry) continue;
    const stored = (row.properties ?? {}) as Record<string, unknown>;
    const properties: SoilSurveyProperties = {
      mukey: typeof stored.mukey === "string" ? stored.mukey : "",
      muname: storedText(stored.muname),
      soilSeries: storedText(stored.soilSeries),
      drainageClass: storedText(stored.drainageClass),
      hydric: typeof stored.hydric === "boolean" ? stored.hydric : null,
      landCapabilityClass: storedText(stored.landCapabilityClass),
      areaSymbol: storedText(stored.areaSymbol),
      surveyAreaVintage: storedText(stored.surveyAreaVintage),
    };
    features.push({
      type: "Feature",
      id: row.polygon_key ?? undefined,
      geometry: JSON.parse(row.geometry) as GeoJSON.Geometry,
      properties,
    });
  }
  return { features, truncated: rows.length > MAX_SOIL_POLYGONS };
}

/**
 * Merges the stored map units intersecting the viewport by drainage class.
 *
 * One local query where the proxied path made up to nine SDA round trips — the measured
 * 30-40 s an averaged view used to cost. Each delineation is simplified *before* the
 * union rather than after: the union is the expensive step, and feeding it generalized
 * input is what makes a multi-degree viewport answerable at all.
 *
 * `toleranceDegrees` lands directly in `ST_SimplifyPreserveTopology`'s own typed
 * `double precision` argument, and the envelope bounds in `ST_MakeEnvelope`'s, so
 * neither needs a `::numeric` guard against postgres-js binding a fractional parameter
 * as the bigint sitting next to it — see `environmental-read-model.ts`
 * §OBSERVATION_DENSITY_FLOOR_FRACTION for the case that does. `hydricFraction` is
 * divided in TypeScript for the same reason: the two counts it divides are bigints.
 */
async function readAggregatedFeatures(
  bounds: [number, number, number, number],
  toleranceDegrees: number
): Promise<{ features: GeoJSON.Feature[]; truncated: boolean }> {
  const [west, south, east, north] = bounds;
  const rows = await db.execute<{
    drainage_class: string;
    geometry: string | null;
    map_unit_count: string;
    hydric_count: string;
    rated_count: string;
    input_rows: string;
  }>(sql`
    WITH candidate AS (
      SELECT
        COALESCE(f.properties->>'drainageClass', 'unknown') AS drainage_class,
        CASE
          WHEN jsonb_typeof(f.properties->'hydric') = 'boolean'
            THEN (f.properties->>'hydric')::boolean
        END AS hydric,
        ST_SimplifyPreserveTopology(f.geom, ${toleranceDegrees}) AS geom
      FROM geo.features f
      JOIN geo.layers l ON l.id = f.layer_id
      WHERE l.name = ${SOIL_SURVEY_LAYER_NAME}
        AND f.status = 'published'
        AND f.geom IS NOT NULL
        AND f.geom && ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)
      LIMIT ${MAX_SOIL_AGGREGATE_INPUT_ROWS + 1}
    )
    SELECT
      drainage_class,
      ST_AsGeoJSON(
        ST_CollectionExtract(ST_MakeValid(ST_Union(geom)), 3)
      ) AS geometry,
      COUNT(*) AS map_unit_count,
      COUNT(*) FILTER (WHERE hydric) AS hydric_count,
      COUNT(*) FILTER (WHERE hydric IS NOT NULL) AS rated_count,
      (SELECT COUNT(*) FROM candidate) AS input_rows
    FROM candidate
    GROUP BY drainage_class
  `);

  const features: GeoJSON.Feature[] = [];
  let inputRows = 0;
  for (const row of rows) {
    inputRows = Number(row.input_rows);
    // A degenerate union that left no polygon-typed result (every input row in the class
    // was a sliver ST_MakeValid collapsed to empty) is skipped rather than emitted as a
    // feature with no geometry.
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
    const geometry = JSON.parse(row.geometry) as GeoJSON.Geometry;
    if (geometry.type === "GeometryCollection" || !("coordinates" in geometry)) continue;
    if ((geometry.coordinates as unknown[]).length === 0) continue;
    features.push({ type: "Feature", geometry, properties });
  }
  return { features, truncated: inputRows > MAX_SOIL_AGGREGATE_INPUT_ROWS };
}

/** A stored JSONB string, or null — never a coerced non-string. */
function storedText(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

export interface SoilSurveyBackfillOptions {
  /** "west,south,east,north". The PNW envelope is `-125,42,-111,49`. */
  bbox: string;
  /** Hard stop on cells fetched this run — what makes the backfill bounded. */
  maxCells: number;
  /** SDA fetches in flight. Left at the read path's bound unless told otherwise. */
  concurrency?: number;
  /** Called after each cell so a long run is observable while it runs. */
  onCell?: (result: SoilSurveyBackfillCellResult) => void;
}

export interface SoilSurveyBackfillCellResult {
  cellKey: string;
  polygonCount: number;
  unreadableCount: number;
  truncated: boolean;
  elapsedMs: number;
  error: string | null;
}

export interface SoilSurveyBackfillReport {
  /** Cells the envelope covers in total. */
  cellsInEnvelope: number;
  /** …already in the ledger before this run, and therefore skipped. */
  alreadyCovered: number;
  /** …attempted this run, at most `maxCells`. */
  attempted: number;
  /** …that failed; they stay uncovered and a later run retries them. */
  failed: number;
  polygonsStored: number;
  unreadableRows: number;
  elapsedMs: number;
  /** True when cells remain — run again to continue. */
  remaining: number;
  cells: SoilSurveyBackfillCellResult[];
}

/**
 * Populates coverage for an envelope, bounded and resumable.
 *
 * Bounded by `maxCells`; resumable because the coverage ledger *is* the cursor — a cell
 * with a row is skipped, so re-running continues where the last run stopped with no
 * state of its own. A failed cell is left uncovered rather than recorded, so a retry
 * picks it up and a transport fault never becomes a claim about the ground.
 *
 * Measured cost per cell and the resulting envelope projection live in
 * `src/lib/server/AGENTS.md` §soil-survey-persistence. The envelope is large: do not
 * run it unattended in one pass.
 */
export async function backfillSoilSurvey(
  options: SoilSurveyBackfillOptions
): Promise<SoilSurveyBackfillReport> {
  const bounds = parseBbox(options.bbox);
  const envelope = soilSurveyCellsCovering(bounds);
  const covered = await soilSurveyCoveredCellKeys(envelope);
  const pending = envelope.filter((cell) => !covered.has(cell.key));
  const batch = pending.slice(0, Math.max(options.maxCells, 0));

  const startedAt = Date.now();
  const results = await mapWithConcurrency(
    batch,
    options.concurrency ?? SDA_CONCURRENCY,
    async (cell): Promise<SoilSurveyBackfillCellResult> => {
      const cellStartedAt = Date.now();
      try {
        const row = await ingestSoilSurveyCell(cell);
        const result: SoilSurveyBackfillCellResult = {
          cellKey: cell.key,
          polygonCount: row?.polygonCount ?? 0,
          unreadableCount: row?.unreadableCount ?? 0,
          truncated: row?.truncated ?? false,
          elapsedMs: Date.now() - cellStartedAt,
          error: null,
        };
        options.onCell?.(result);
        return result;
      } catch (error) {
        const result: SoilSurveyBackfillCellResult = {
          cellKey: cell.key,
          polygonCount: 0,
          unreadableCount: 0,
          truncated: false,
          elapsedMs: Date.now() - cellStartedAt,
          error: error instanceof Error ? error.message : String(error),
        };
        options.onCell?.(result);
        return result;
      }
    }
  );

  const failed = results.filter((result) => result.error !== null).length;
  return {
    cellsInEnvelope: envelope.length,
    alreadyCovered: covered.size,
    attempted: results.length,
    failed,
    polygonsStored: results.reduce((total, result) => total + result.polygonCount, 0),
    unreadableRows: results.reduce((total, result) => total + result.unreadableCount, 0),
    elapsedMs: Date.now() - startedAt,
    remaining: pending.length - (results.length - failed),
    cells: results,
  };
}

/**
 * Rejects anything that is not four finite WGS84 numbers. The router validates too,
 * but these values are interpolated into SDA's SQL, so the service refuses to trust
 * them. A `RangeError` rather than a `SoilSurveyResponseError`: a bad bbox is our bug,
 * and reporting it as an upstream fault would blame SDA for a request we never sent.
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
