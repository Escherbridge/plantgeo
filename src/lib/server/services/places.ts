// Spatial predicates are unimplemented here: the `_`-prefixed parameters below are accepted
// and ignored, so each reader is an unfiltered top-50 scan of `geo.poi`. The module has had no
// caller since it was written -- see the conformity c3 proof packet before wiring one up.
import { db } from "@/lib/server/db";
import { poi } from "@/lib/server/db/schema";
import { sql } from "drizzle-orm";

export async function searchByCategory(category: string, _bbox: { west: number; south: number; east: number; north: number }) {
  return db.select().from(poi).where(
    sql`${poi.category} = ${category}`
  ).limit(50);
}

export async function searchNearby(_lat: number, _lon: number, _radius: number, limit: number) {
  return db.select().from(poi).limit(limit);
}

export async function searchByText(query: string, _bbox?: { west: number; south: number; east: number; north: number }) {
  return db.select().from(poi).where(sql`${poi.name} ILIKE ${'%' + query + '%'}`).limit(50);
}

export async function getById(id: string) {
  const results = await db.select().from(poi).where(sql`${poi.id} = ${id}`).limit(1);
  return results[0] ?? null;
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
