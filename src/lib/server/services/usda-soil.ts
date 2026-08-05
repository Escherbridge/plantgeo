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
 * `src/lib/server/AGENTS.md` §soil-survey for the two sites it was measured over.
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

export interface SoilSurveyCollection extends GeoJSON.FeatureCollection {
  /** Upstream held more map units than were served; the view is a subset. */
  truncated: boolean;
  /**
   * Rows SDA served whose geometry this reader could not parse and therefore dropped.
   * Non-zero means the view is incomplete for a reason USDA never reported, so it must
   * not be presented as an absence of soil — the same honest gap `truncated` reports.
   */
  unreadableGeometries: number;
}

/** Soil Data Access answered, but not with a result table this module can read. */
export class SoilSurveyResponseError extends Error {}

/** Column aliases the query sets and the reader looks up by name. */
const MUKEY_COLUMN = "mukey";
const GEOMETRY_COLUMN = "geom";

/**
 * SSURGO map units clipped to the viewport, joined to their dominant component's
 * ratings. One round trip; see `src/lib/server/AGENTS.md` §soil-survey.
 * @param bbox "west,south,east,north"
 */
export async function getSoilSurvey(bbox: string): Promise<SoilSurveyCollection> {
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

  return { type: "FeatureCollection", features, truncated, unreadableGeometries };
}

/**
 * Guard for a cached payload, which is untrusted like any other external value.
 * `unreadableGeometries` is required, so an entry written before this field existed is
 * re-fetched rather than served: it cannot say how many rows it dropped, and defaulting
 * it to zero would assert a completeness nobody measured.
 */
function isSoilSurveyCollection(value: unknown): value is SoilSurveyCollection {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as {
    features?: unknown;
    truncated?: unknown;
    unreadableGeometries?: unknown;
  };
  return (
    Array.isArray(candidate.features) &&
    typeof candidate.truncated === "boolean" &&
    typeof candidate.unreadableGeometries === "number"
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
