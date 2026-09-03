/**
 * POI readers for `placesRouter` (`src/lib/server/trpc/routers/places.ts`), which bounds every
 * input. `geo.poi` has no producer yet -- these are build-ahead readers over an empty table.
 * See `src/lib/server/AGENTS.md` §places.
 */
import { db } from "@/lib/server/db";
import { poi } from "@/lib/server/db/schema";
import { and, eq, sql, type SQL } from "drizzle-orm";

/** Rows a reader whose caller sets no limit will return before it reports itself truncated. */
export const MAX_PLACE_RESULTS = 50;

/** A viewport in EPSG:4326 degrees; `placesRouter` rejects an unordered or out-of-range one. */
export interface PlacesBoundingBox {
  west: number;
  south: number;
  east: number;
  north: number;
}

/** One POI row as every reader below returns it. */
export interface Place {
  id: string;
  name: string;
  longitude: number | null;
  latitude: number | null;
  category: string | null;
  subcategory: string | null;
  address: string | null;
  phone: string | null;
  website: string | null;
  hours: unknown;
  tags: unknown;
}

/** A capped list plus whether the cap actually cut anything off it. */
export interface PlaceResults {
  places: Place[];
  truncated: boolean;
}

/** `searchNearby` adds the metres it measured, so the caller never re-derives them. */
export interface NearbyPlaceResults {
  places: (Place & { distanceMeters: number })[];
  truncated: boolean;
}

/**
 * Every column a places response carries. `geom` is projected to two numbers rather than
 * selected: `db.select()` returned it as WKB hex, which no caller can read and every row pays
 * for -- the same projection `visualization.ts` documents for `geo.features.properties`.
 */
const placeColumns = {
  id: poi.id,
  name: poi.name,
  category: poi.category,
  subcategory: poi.subcategory,
  address: poi.address,
  phone: poi.phone,
  website: poi.website,
  hours: poi.hours,
  tags: poi.tags,
  longitude: sql<number | null>`ST_X(${poi.geom})`,
  latitude: sql<number | null>`ST_Y(${poi.geom})`,
};

/**
 * Envelope overlap, which is the predicate `idx_poi_geom` (GiST on `geo.poi.geom`, drizzle/0001)
 * actually answers -- same operator `readPublishedFirePerimeters` uses on `geo.features.geom`.
 */
function withinBoundingBox(bbox: PlacesBoundingBox): SQL {
  return sql`${poi.geom} && ST_MakeEnvelope(${bbox.west}, ${bbox.south}, ${bbox.east}, ${bbox.north}, 4326)`;
}

/** Neutralises the ILIKE wildcards `%`, `_` and `\` in user text, which is a pattern here. */
export function escapeLikeWildcards(text: string): string {
  return text.replace(/[\\%_]/g, "\\$&");
}

/** Splits one over-fetched row off a capped read; see `PlaceResults.truncated`. */
function capResults<Row>(rows: Row[], limit: number): { rows: Row[]; truncated: boolean } {
  return { rows: rows.slice(0, limit), truncated: rows.length > limit };
}

/** POIs of one category inside the viewport, capped at `MAX_PLACE_RESULTS`. */
export async function searchByCategory(
  category: string,
  bbox: PlacesBoundingBox
): Promise<PlaceResults> {
  const rows = await db
    .select(placeColumns)
    .from(poi)
    .where(and(eq(poi.category, category), withinBoundingBox(bbox)))
    .limit(MAX_PLACE_RESULTS + 1);
  const capped = capResults(rows, MAX_PLACE_RESULTS);
  return { places: capped.rows, truncated: capped.truncated };
}

/**
 * POIs within `radiusMeters` of a point, nearest first.
 *
 * Measured on the geography cast rather than in degrees, so the radius is metres everywhere on
 * the globe -- the same `ST_DWithin`/`ST_Distance` pair `readCommunityProposals` uses. A row
 * whose `geom` is null is not near anything and drops out here, which is the correct answer.
 */
export async function searchNearby(
  latitude: number,
  longitude: number,
  radiusMeters: number,
  limit: number
): Promise<NearbyPlaceResults> {
  const centre = sql`ST_SetSRID(ST_MakePoint(${longitude}, ${latitude}), 4326)::geography`;
  // `idx_poi_geom` is a GiST on the GEOMETRY column, so it cannot answer ST_DWithin's geography
  // cast directly. Buffering the centre by the radius and testing envelope overlap first gives
  // the planner an index-backed candidate set; ST_DWithin below still does the exact geodesic
  // test, this only avoids running it against every row in the table.
  const withinRadiusEnvelope = sql`${poi.geom} && ST_Envelope(ST_Buffer(${centre}, ${radiusMeters})::geometry)`;
  const withinExactRadius = sql`ST_DWithin(${poi.geom}::geography, ${centre}, ${radiusMeters})`;
  const rows = await db
    .select({
      ...placeColumns,
      distanceMeters: sql<number>`ST_Distance(${poi.geom}::geography, ${centre})`,
    })
    .from(poi)
    .where(and(withinRadiusEnvelope, withinExactRadius))
    .orderBy(sql`ST_Distance(${poi.geom}::geography, ${centre})`)
    .limit(limit + 1);
  const capped = capResults(rows, limit);
  return {
    places: capped.rows.map((row) => ({ ...row, distanceMeters: Number(row.distanceMeters) })),
    truncated: capped.truncated,
  };
}

/**
 * POIs whose name contains `query`, inside the viewport.
 *
 * `bbox` is required, not optional: `ILIKE '%…%'` cannot use an index, so an unbounded call is a
 * full table scan an anonymous caller could repeat without limit. `placesRouter` has zero
 * consumers today, so this is a signature change with nothing left to update at the call site.
 */
export async function searchByText(
  query: string,
  bbox: PlacesBoundingBox
): Promise<PlaceResults> {
  const nameMatches = sql`${poi.name} ILIKE ${`%${escapeLikeWildcards(query)}%`}`;
  const rows = await db
    .select(placeColumns)
    .from(poi)
    .where(and(nameMatches, withinBoundingBox(bbox)))
    .limit(MAX_PLACE_RESULTS + 1);
  const capped = capResults(rows, MAX_PLACE_RESULTS);
  return { places: capped.rows, truncated: capped.truncated };
}

/** One POI by id, or null when nothing carries it. */
export async function getById(id: string): Promise<Place | null> {
  const rows = await db.select(placeColumns).from(poi).where(eq(poi.id, id)).limit(1);
  return rows[0] ?? null;
}

export const POI_CATEGORIES = [
  { id: 'restaurants', label: 'Restaurants', icon: 'Utensils' },
  { id: 'shops', label: 'Shops', icon: 'ShoppingBag' },
  { id: 'parks', label: 'Parks', icon: 'Trees' },
  { id: 'transit', label: 'Transit', icon: 'Bus' },
  { id: 'hospitals', label: 'Hospitals', icon: 'Hospital' },
  { id: 'schools', label: 'Schools', icon: 'School' },
  { id: 'fire_stations', label: 'Fire Stations', icon: 'Flame' },
  { id: 'water_sources', label: 'Water Sources', icon: 'Droplets' },
];
