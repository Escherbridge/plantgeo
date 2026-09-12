/** Parquet-only POI contract; the retired `geo.poi` relation is intentionally not queried. */
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

/** Neutralises the ILIKE wildcards `%`, `_` and `\` in user text, which is a pattern here. */
export function escapeLikeWildcards(text: string): string {
  return text.replace(/[\\%_]/g, "\\$&");
}

/** Typed empty result until a governed places lane is admitted. */
export async function searchByCategory(
  _category: string,
  _bbox: PlacesBoundingBox
): Promise<PlaceResults> {
  return { places: [], truncated: false };
}

/** Typed empty result until a governed places lane is admitted. */
export async function searchNearby(
  _latitude: number,
  _longitude: number,
  _radiusMeters: number,
  _limit: number
): Promise<NearbyPlaceResults> {
  return { places: [], truncated: false };
}

/** Typed empty result until a governed places lane is admitted. */
export async function searchByText(
  _query: string,
  _bbox: PlacesBoundingBox
): Promise<PlaceResults> {
  return { places: [], truncated: false };
}

/** One POI by id, or null when nothing carries it. */
export async function getById(_id: string): Promise<Place | null> {
  return null;
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
