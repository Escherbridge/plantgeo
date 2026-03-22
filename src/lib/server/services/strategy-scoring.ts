import { getSoilProperties } from "@/lib/server/services/soilgrids";
import { getStreamflowGauges } from "@/lib/server/services/usgs-water";
import { getDroughtClassification } from "@/lib/server/services/drought";

export interface StrategyScore {
  strategyId: string;
  name: string;
  score: number; // 0-100 composite
  factors: {
    waterStress: number;
    soilHealth: number;
    fireRisk: number;
    vegetationDegradation: number;
    communityDemand: number;
  };
  confidence: "high" | "medium" | "low";
  topReasons: string[];
}

type StrategyId =
  | "keyline"
  | "silvopasture"
  | "reforestation"
  | "biochar"
  | "water_harvesting"
  | "cover_cropping";

const STRATEGY_META: Record<StrategyId, { name: string; category: string }> = {
  keyline: { name: "Keyline Design", category: "Water Management" },
  silvopasture: { name: "Silvopasture", category: "Agroforestry" },
  reforestation: { name: "Reforestation", category: "Ecosystem Restoration" },
  biochar: { name: "Biochar Application", category: "Soil Carbon" },
  water_harvesting: { name: "Water Harvesting & Swales", category: "Water Management" },
  cover_cropping: { name: "Cover Cropping", category: "Soil Health" },
};

// Scoring weights per strategy: waterStress, soilHealth, fireRisk, vegetationDegradation, communityDemand
const STRATEGY_WEIGHTS: Record<StrategyId, Record<string, number>> = {
  keyline: {
    waterStress: 0.4,
    soilHealth: 0.2,
    fireRisk: 0.1,
    vegetationDegradation: 0.2,
    communityDemand: 0.1,
  },
  silvopasture: {
    waterStress: 0.2,
    soilHealth: 0.3,
    fireRisk: 0.2,
    vegetationDegradation: 0.2,
    communityDemand: 0.1,
  },
  reforestation: {
    waterStress: 0.15,
    soilHealth: 0.25,
    fireRisk: 0.25,
    vegetationDegradation: 0.3,
    communityDemand: 0.05,
  },
  biochar: {
    waterStress: 0.1,
    soilHealth: 0.45,
    fireRisk: 0.1,
    vegetationDegradation: 0.2,
    communityDemand: 0.15,
  },
  water_harvesting: {
    waterStress: 0.5,
    soilHealth: 0.15,
    fireRisk: 0.05,
    vegetationDegradation: 0.15,
    communityDemand: 0.15,
  },
  cover_cropping: {
    waterStress: 0.2,
    soilHealth: 0.4,
    fireRisk: 0.05,
    vegetationDegradation: 0.25,
    communityDemand: 0.1,
  },
};

interface InputFactors {
  waterStress: number;        // 0-100 (100 = most stressed)
  soilHealth: number;         // 0-100 (100 = most degraded / most needing intervention)
  fireRisk: number;           // 0-100 (100 = highest fire risk)
  vegetationDegradation: number; // 0-100 (100 = most degraded)
  communityDemand: number;    // 0-100 (100 = highest demand in area)
}

function buildTopReasons(
  strategyId: StrategyId,
  factors: InputFactors
): string[] {
  const reasons: Array<{ weight: number; reason: string }> = [];
  const weights = STRATEGY_WEIGHTS[strategyId];

  if (factors.waterStress > 60) {
    reasons.push({
      weight: weights.waterStress * factors.waterStress,
      reason: `High water stress (${factors.waterStress.toFixed(0)}/100) favors water management strategies`,
    });
  }
  if (factors.soilHealth > 50) {
    reasons.push({
      weight: weights.soilHealth * factors.soilHealth,
      reason: `Degraded soil health (${factors.soilHealth.toFixed(0)}/100) indicates strong intervention need`,
    });
  }
  if (factors.fireRisk > 55) {
    reasons.push({
      weight: weights.fireRisk * factors.fireRisk,
      reason: `Elevated fire risk (${factors.fireRisk.toFixed(0)}/100) increases urgency`,
    });
  }
  if (factors.vegetationDegradation > 55) {
    reasons.push({
      weight: weights.vegetationDegradation * factors.vegetationDegradation,
      reason: `Significant vegetation degradation (${factors.vegetationDegradation.toFixed(0)}/100) signals restoration opportunity`,
    });
  }
  if (factors.communityDemand > 40) {
    reasons.push({
      weight: weights.communityDemand * factors.communityDemand,
      reason: `Community priority zone nearby with local demand for this strategy`,
    });
  }

  // Sort by contribution weight and return top 3
  reasons.sort((a, b) => b.weight - a.weight);
  const top = reasons.slice(0, 3).map((r) => r.reason);

  // Fallback reason if nothing triggered
  if (top.length === 0) {
    const meta = STRATEGY_META[strategyId];
    top.push(`${meta.name} is applicable under current environmental conditions`);
  }

  return top;
}

function computeCompositeScore(
  strategyId: StrategyId,
  factors: InputFactors
): number {
  const weights = STRATEGY_WEIGHTS[strategyId];
  const score =
    factors.waterStress * weights.waterStress +
    factors.soilHealth * weights.soilHealth +
    factors.fireRisk * weights.fireRisk +
    factors.vegetationDegradation * weights.vegetationDegradation +
    factors.communityDemand * weights.communityDemand;
  return Math.round(Math.min(100, Math.max(0, score)));
}

function deriveConfidence(dataAvailable: { soil: boolean; weather: boolean }): "high" | "medium" | "low" {
  if (dataAvailable.soil && dataAvailable.weather) return "high";
  if (dataAvailable.soil || dataAvailable.weather) return "medium";
  return "low";
}

/**
 * Normalize soil organic carbon (g/kg) to a soil degradation score 0-100.
 * Low SOC = degraded soils = higher intervention need.
 * Reference: <5 g/kg is severely depleted, >30 g/kg is healthy.
 */
function socToSoilScore(soc: number): number {
  if (soc <= 0) return 90;
  if (soc >= 30) return 10;
  // Inverse linear: low SOC → high score
  return Math.round(100 - (soc / 30) * 90);
}

/**
 * Fetch environmental input factors for a lat/lon.
 * Uses SoilGrids for soil health; other factors use deterministic mock values
 * derived from coordinates until Track 21–23 services are wired.
 *
 * TODO(Track 21): Replace fireRisk mock with calculateFireRisk from fire-risk service
 * TODO(Track 22): Replace waterStress mock with getWaterScarcityScore from drought/USGS services
 * TODO(Track 23): Replace vegetationDegradation mock with NDVI anomaly from vegetation service
 * TODO(Track 25): Replace communityDemand mock with getPriorityZones PostGIS overlap check
 */
/**
 * Derive water stress (0-100) from USDM drought classification at a point.
 * The USDM GeoJSON features carry a numeric `DM` property: 0=D0 … 4=D4.
 * We pick the highest DM value whose polygon contains the point (simple
 * bounding-box test — good enough for scoring purposes).
 */
function droughtScoreFromGeoJSON(
  geojson: GeoJSON.FeatureCollection,
  lat: number,
  lon: number
): number | null {
  let maxDm = -1;
  for (const feature of geojson.features) {
    const dm = typeof feature.properties?.DM === "number" ? feature.properties.DM : -1;
    if (dm <= maxDm) continue;
    // Fast bounding-box containment check on each polygon ring
    const geom = feature.geometry;
    if (!geom) continue;
    const rings: number[][][] =
      geom.type === "Polygon"
        ? (geom as GeoJSON.Polygon).coordinates
        : geom.type === "MultiPolygon"
        ? (geom as GeoJSON.MultiPolygon).coordinates.flat()
        : [];
    for (const ring of rings) {
      // Bounding box of ring
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
  if (maxDm < 0) return null;
  // Map DM 0-4 → water stress 40-100
  return Math.round(40 + maxDm * 15);
}

/**
 * Derive water stress (0-100) from nearby USGS streamflow gauge condition.
 * Finds the nearest gauge within a ~2° bounding box.
 */
function waterStressFromGauges(
  gauges: Array<{ lat: number; lon: number; condition: string; percentile: number | null }>,
  lat: number,
  lon: number
): number | null {
  if (gauges.length === 0) return null;
  let nearest: (typeof gauges)[0] | null = null;
  let minDist = Infinity;
  for (const g of gauges) {
    const d = Math.hypot(g.lat - lat, g.lon - lon);
    if (d < minDist) { minDist = d; nearest = g; }
  }
  if (!nearest || minDist > 2) return null;
  // percentile-based: low percentile = high stress
  if (nearest.percentile !== null) {
    return Math.round(100 - nearest.percentile);
  }
  // Condition-based fallback
  const conditionMap: Record<string, number> = {
    above_normal: 20,
    normal: 35,
    below_normal: 55,
    low: 70,
    critically_low: 90,
    unknown: 50,
  };
  return conditionMap[nearest.condition] ?? 50;
}

async function fetchInputFactors(
  lat: number,
  lon: number
): Promise<{ factors: InputFactors; dataAvailable: { soil: boolean; weather: boolean } }> {
  let soilScore = 50;
  let soilAvailable = false;

  try {
    const soil = await getSoilProperties(lat, lon);
    soilScore = socToSoilScore(soil.organicCarbon);
    soilAvailable = true;
  } catch {
    // SoilGrids unavailable — use location-based estimate
    const latFactor = Math.abs(lat - 37) / 20;
    const lonFactor = Math.abs(lon + 115) / 30;
    soilScore = Math.min(90, 40 + latFactor * 20 + lonFactor * 15);
  }

  // Fetch water stress from real services in parallel
  const bbox = `${(lon - 2).toFixed(2)},${(lat - 2).toFixed(2)},${(lon + 2).toFixed(2)},${(lat + 2).toFixed(2)}`;
  const [droughtResult, gaugesResult] = await Promise.allSettled([
    getDroughtClassification(),
    getStreamflowGauges(bbox),
  ]);

  let waterStress = 50; // neutral fallback
  let weatherAvailable = false;

  if (droughtResult.status === "fulfilled") {
    const droughtScore = droughtScoreFromGeoJSON(droughtResult.value, lat, lon);
    if (droughtScore !== null) {
      waterStress = droughtScore;
      weatherAvailable = true;
    }
  }
  // Streamflow refines the estimate if drought didn't place the point
  if (!weatherAvailable && gaugesResult.status === "fulfilled") {
    const gaugeScore = waterStressFromGauges(gaugesResult.value, lat, lon);
    if (gaugeScore !== null) {
      waterStress = gaugeScore;
      weatherAvailable = true;
    }
  }
  // If both services available, blend them
  if (
    weatherAvailable &&
    droughtResult.status === "fulfilled" &&
    gaugesResult.status === "fulfilled"
  ) {
    const droughtScore = droughtScoreFromGeoJSON(droughtResult.value, lat, lon);
    const gaugeScore = waterStressFromGauges(gaugesResult.value, lat, lon);
    if (droughtScore !== null && gaugeScore !== null) {
      waterStress = Math.round(droughtScore * 0.6 + gaugeScore * 0.4);
    }
  }

  // Fallback coordinate-based estimate when both services fail
  if (!weatherAvailable) {
    const normalizedLon = Math.max(0, Math.min(1, (-lon - 100) / 25));
    waterStress = Math.round(30 + normalizedLon * 50 + (Math.sin(lat * 0.3) + 1) * 10);
  }

  // TODO(Track 21): replace with calculateFireRisk from fire-risk service
  const fireRisk = Math.round(
    25 +
      Math.max(0, (36 - lat) * 1.5) +
      Math.max(0, (-lon - 117) * 2) +
      (Math.cos(lon * 0.15) + 1) * 8
  );

  // TODO(Track 23): replace with NDVI anomaly from vegetation service
  const vegetationDegradation = Math.round(
    20 + soilScore * 0.4 + (Math.sin(lat * 0.2 + lon * 0.1) + 1) * 10
  );

  // TODO(Track 25): replace with getPriorityZones PostGIS overlap check
  const communityDemand = Math.round(30 + (Math.cos(lat * 0.5) + 1) * 20);

  return {
    factors: {
      waterStress: Math.min(100, Math.max(0, waterStress)),
      soilHealth: Math.min(100, Math.max(0, soilScore)),
      fireRisk: Math.min(100, Math.max(0, fireRisk)),
      vegetationDegradation: Math.min(100, Math.max(0, vegetationDegradation)),
      communityDemand: Math.min(100, Math.max(0, communityDemand)),
    },
    dataAvailable: { soil: soilAvailable, weather: weatherAvailable },
  };
}

/**
 * Generate ranked strategy recommendations for a lat/lon location.
 * Fetches environmental indicators in parallel, scores all 6 strategy types,
 * and returns them sorted by composite score descending.
 */
export async function getStrategyRecommendations(
  lat: number,
  lon: number
): Promise<StrategyScore[]> {
  const { factors, dataAvailable } = await fetchInputFactors(lat, lon);
  const confidence = deriveConfidence(dataAvailable);

  const strategies = (Object.keys(STRATEGY_WEIGHTS) as StrategyId[]).map(
    (strategyId) => {
      const score = computeCompositeScore(strategyId, factors);
      const topReasons = buildTopReasons(strategyId, factors);
      const meta = STRATEGY_META[strategyId];

      return {
        strategyId,
        name: meta.name,
        score,
        factors: { ...factors },
        confidence,
        topReasons,
      };
    }
  );

  // Sort descending by score
  strategies.sort((a, b) => b.score - a.score);

  return strategies;
}
