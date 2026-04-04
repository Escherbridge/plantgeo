import { db } from "@/lib/server/db";
import { 
  historicalFireData, 
  historicalWaterDrought, 
  historicalVegetation,
  openPlantData,
  openToolingData
} from "@/lib/server/db/schema";
import { sql, and, gte, lte } from "drizzle-orm";

interface EnvironmentalContext {
  fireRiskTrend: number;
  waterScarcityAvg: number;
  vegetationHealthAvg: number;
  recentAnomalies: number;
}

/**
 * Retrieves historical environmental data within a bounding box centered on a lat/lon.
 * MVP: Uses coordinate math to approximate radius before full PostGIS geom migration is run.
 */
export async function getEnvironmentalContext(
  lat: number, 
  lon: number, 
  radiusKm: number = 50
): Promise<EnvironmentalContext> {
  // Rough bounding box: 1 degree latitude is ~111km
  const latDelta = radiusKm / 111.0;
  const lonDelta = radiusKm / (111.0 * Math.cos(lat * (Math.PI / 180)));

  const minLat = lat - latDelta;
  const maxLat = lat + latDelta;
  const minLon = lon - lonDelta;
  const maxLon = lon + lonDelta;

  // Retrieve Fire Data
  const fireRecords = await db
    .select()
    .from(historicalFireData)
    .where(
      and(
        gte(historicalFireData.lat, minLat),
        lte(historicalFireData.lat, maxLat),
        gte(historicalFireData.lon, minLon),
        lte(historicalFireData.lon, maxLon)
      )
    )
    .limit(100);

  // Retrieve Water Data
  const waterRecords = await db
    .select()
    .from(historicalWaterDrought)
    .where(
      and(
        gte(historicalWaterDrought.lat, minLat),
        lte(historicalWaterDrought.lat, maxLat),
        gte(historicalWaterDrought.lon, minLon),
        lte(historicalWaterDrought.lon, maxLon)
      )
    )
    .limit(100);

  // Retrieve Veg Data
  const vegRecords = await db
    .select()
    .from(historicalVegetation)
    .where(
      and(
        gte(historicalVegetation.lat, minLat),
        lte(historicalVegetation.lat, maxLat),
        gte(historicalVegetation.lon, minLon),
        lte(historicalVegetation.lon, maxLon)
      )
    )
    .limit(100);

  // Compute mock aggregated metrics
  const fireRiskTrend = fireRecords.length > 0 
    ? fireRecords.reduce((sum, r) => sum + (r.fire_risk_score || 0), 0) / fireRecords.length
    : 0;
    
  const anomalies = fireRecords.reduce((sum, r) => sum + (r.detected_anomalies || 0), 0);
  
  const waterScarcityAvg = waterRecords.length > 0
    ? waterRecords.reduce((sum, r) => sum + (r.water_scarcity_index || 0), 0) / waterRecords.length
    : 0.5; // default moderate
    
  const vegetationHealthAvg = vegRecords.length > 0
    ? vegRecords.reduce((sum, r) => sum + (r.ndvi_value || 0), 0) / vegRecords.length
    : 0.5;

  return {
    fireRiskTrend,
    waterScarcityAvg,
    vegetationHealthAvg,
    recentAnomalies: anomalies
  };
}

/**
 * Searches local open tables for plants/tools matching the context
 */
export async function queryLocalAgricultureSolutions(context: EnvironmentalContext) {
  // A genuine implementation would use Postgres similarity or precise JSONB query
  // For now, we mock the return from the open structure
  const plants = await db.select().from(openPlantData).limit(5);
  const tools = await db.select().from(openToolingData).limit(5);
  
  return {
    recommendedPlants: plants,
    recommendedTooling: tools
  };
}
