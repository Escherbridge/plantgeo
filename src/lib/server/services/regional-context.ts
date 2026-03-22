// Assembles all regional environmental data for a given lat/lon.
// Uses geohash-precision-5 Redis caching with 15-min TTL.

import { getRedis } from '@/lib/server/redis';
import { getStrategyRecommendations, type StrategyScore } from '@/lib/server/services/strategy-scoring';
import { getSoilProperties, type SoilProperties } from '@/lib/server/services/soilgrids';
import { getDroughtClassification } from '@/lib/server/services/drought';
import { getStreamflowGauges, type WaterGauge } from '@/lib/server/services/usgs-water';
import { getMTBSPerimeters } from '@/lib/server/services/mtbs';
import { getInterventionSuitability, type InterventionSuitability } from '@/lib/server/services/carbon-potential';

// Simple geohash approximation (precision 5 ≈ 5km box)
function geohash5(lat: number, lon: number): string {
  return `${lat.toFixed(2)}_${lon.toFixed(2)}`;
}

export interface RegionalContextPayload {
  location: { lat: number; lon: number; geohash: string };
  strategyRecommendations: StrategyScore[] | null;
  soilProperties: SoilProperties | null;
  waterScarcity: {
    droughtClass: string | null;
    nearestGauge: WaterGauge | null;
  } | null;
  mtbsPerimeters: { fires: GeoJSON.Feature[]; totalCount: number } | null;
  carbonPotential: InterventionSuitability | null;
}

export interface RegionalContextResult {
  payload: RegionalContextPayload;
  dataFreshness: Record<string, string>;
  cacheHit: boolean;
}

function extractDroughtClass(
  geojson: GeoJSON.FeatureCollection,
  lat: number,
  lon: number
): string | null {
  const DM_LABELS: Record<number, string> = {
    0: 'D0 (Abnormally Dry)',
    1: 'D1 (Moderate Drought)',
    2: 'D2 (Severe Drought)',
    3: 'D3 (Extreme Drought)',
    4: 'D4 (Exceptional Drought)',
  };

  let maxDm = -1;
  for (const feature of geojson.features) {
    const dm =
      typeof feature.properties?.DM === 'number' ? feature.properties.DM : -1;
    if (dm <= maxDm) continue;
    const geom = feature.geometry;
    if (!geom) continue;
    const rings: number[][][] =
      geom.type === 'Polygon'
        ? (geom as GeoJSON.Polygon).coordinates
        : geom.type === 'MultiPolygon'
        ? (geom as GeoJSON.MultiPolygon).coordinates.flat()
        : [];
    for (const ring of rings) {
      let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
      for (const [rLon, rLat] of ring) {
        if (rLon < minLon) minLon = rLon;
        if (rLon > maxLon) maxLon = rLon;
        if (rLat < minLat) minLat = rLat;
        if (rLat > maxLat) maxLat = rLat;
      }
      if (lon >= minLon && lon <= maxLon && lat >= minLat && lat <= maxLat) {
        maxDm = dm;
        break;
      }
    }
  }

  return maxDm >= 0 ? (DM_LABELS[maxDm] ?? `D${maxDm}`) : null;
}

function nearestGauge(gauges: WaterGauge[], lat: number, lon: number): WaterGauge | null {
  if (gauges.length === 0) return null;
  let nearest: WaterGauge | null = null;
  let minDist = Infinity;
  for (const g of gauges) {
    const d = Math.hypot(g.lat - lat, g.lon - lon);
    if (d < minDist) {
      minDist = d;
      nearest = g;
    }
  }
  return minDist <= 2 ? nearest : null;
}

export async function assembleRegionalContext(
  lat: number,
  lon: number
): Promise<RegionalContextResult> {
  const gh = geohash5(lat, lon);
  const cacheKey = `ai-context:${gh}`;

  const redis = getRedis();
  try {
    const cached = await redis.get(cacheKey);
    if (cached) {
      const parsed = JSON.parse(cached) as {
        payload: RegionalContextPayload;
        dataFreshness: Record<string, string>;
      };
      return { ...parsed, cacheHit: true };
    }
  } catch {
    // cache miss — proceed to fetch
  }

  const bbox = `${lon - 0.25},${lat - 0.25},${lon + 0.25},${lat + 0.25}`;
  const now = new Date().toISOString();
  const dataFreshness: Record<string, string> = {};

  // Fetch all sources in parallel
  const [
    strategyResult,
    soilResult,
    droughtResult,
    gaugeResult,
    mtbsResult,
    carbonResult,
  ] = await Promise.allSettled([
    getStrategyRecommendations(lat, lon),
    getSoilProperties(lat, lon),
    getDroughtClassification(),
    getStreamflowGauges(bbox),
    getMTBSPerimeters(bbox, new Date().getFullYear() - 30),
    getInterventionSuitability(lat, lon),
  ]);

  // Record data freshness
  dataFreshness.strategyRecommendations =
    strategyResult.status === 'fulfilled' ? now : 'unavailable';
  dataFreshness.soilProperties =
    soilResult.status === 'fulfilled' ? now : 'unavailable';
  dataFreshness.drought =
    droughtResult.status === 'fulfilled' ? now : 'unavailable';
  dataFreshness.streamflow =
    gaugeResult.status === 'fulfilled' ? now : 'unavailable';
  dataFreshness.mtbsPerimeters =
    mtbsResult.status === 'fulfilled' ? now : 'unavailable';
  dataFreshness.carbonPotential =
    carbonResult.status === 'fulfilled' ? now : 'unavailable';

  // Build waterScarcity from drought + gauge results
  let waterScarcity: RegionalContextPayload['waterScarcity'] = null;
  if (
    droughtResult.status === 'fulfilled' ||
    gaugeResult.status === 'fulfilled'
  ) {
    const droughtClass =
      droughtResult.status === 'fulfilled'
        ? extractDroughtClass(droughtResult.value, lat, lon)
        : null;
    const gauge =
      gaugeResult.status === 'fulfilled'
        ? nearestGauge(gaugeResult.value, lat, lon)
        : null;
    waterScarcity = { droughtClass, nearestGauge: gauge };
  }

  // Build MTBS summary
  let mtbsPerimeters: RegionalContextPayload['mtbsPerimeters'] = null;
  if (mtbsResult.status === 'fulfilled') {
    mtbsPerimeters = {
      fires: mtbsResult.value.features,
      totalCount: mtbsResult.value.features.length,
    };
  }

  const payload: RegionalContextPayload = {
    location: { lat, lon, geohash: gh },
    strategyRecommendations:
      strategyResult.status === 'fulfilled' ? strategyResult.value : null,
    soilProperties:
      soilResult.status === 'fulfilled' ? soilResult.value : null,
    waterScarcity,
    mtbsPerimeters,
    carbonPotential:
      carbonResult.status === 'fulfilled' ? carbonResult.value : null,
  };

  // Cache result
  try {
    await redis.set(
      cacheKey,
      JSON.stringify({ payload, dataFreshness }),
      'EX',
      900
    );
  } catch {
    // cache write failure is non-fatal
  }

  // Fail only if ALL sources failed
  const allFailed = [
    strategyResult,
    soilResult,
    droughtResult,
    mtbsResult,
    carbonResult,
  ].every((r) => r.status === 'rejected');
  if (allFailed) throw new Error('All data sources failed');

  return { payload, dataFreshness, cacheHit: false };
}
