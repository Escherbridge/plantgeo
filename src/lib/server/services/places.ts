import { db } from "@/lib/server/db";
import { poi } from "@/lib/server/db/schema";
import { sql } from "drizzle-orm";

export async function searchByCategory(category: string, bbox: { west: number; south: number; east: number; north: number }) {
  return db.select().from(poi).where(
    sql`${poi.category} = ${category}`
  ).limit(50);
}

export async function searchNearby(lat: number, lon: number, radius: number, limit: number) {
  return db.select().from(poi).limit(limit);
}

export async function searchByText(query: string, bbox?: { west: number; south: number; east: number; north: number }) {
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
