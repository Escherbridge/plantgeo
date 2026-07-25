import type { SoilProperties } from "@/lib/server/services/soilgrids";
import {
  getStrategyRecommendations,
  type StrategyScore,
} from "@/lib/server/services/strategy-scoring";
import {
  getPublishedDroughtClassification,
  getPublishedStreamflowGauges,
} from "@/lib/server/services/environmental-read-model";
import {
  getInterventionSuitability,
  type InterventionSuitability,
} from "@/lib/server/services/carbon-potential";
import type { WaterGauge } from "@/lib/server/services/usgs-water";
import { droughtLevelAtPoint } from "@/lib/server/services/alert-engine";
import {
  isRegionalEvidenceSource,
  regionalEvidenceFreshnessState,
} from "@/lib/regional-intelligence";

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

function droughtClassAtPoint(
  collection: GeoJSON.FeatureCollection,
  latitude: number,
  longitude: number
): string | null {
  const labels = [
    "D0 (Abnormally Dry)",
    "D1 (Moderate Drought)",
    "D2 (Severe Drought)",
    "D3 (Extreme Drought)",
    "D4 (Exceptional Drought)",
  ];
  const highest = droughtLevelAtPoint(collection, latitude, longitude);
  return highest !== null ? labels[highest] ?? `D${highest}` : null;
}

function nearestGauge(
  gauges: WaterGauge[],
  latitude: number,
  longitude: number
): WaterGauge | null {
  let nearest: WaterGauge | null = null;
  let distance = Number.POSITIVE_INFINITY;
  for (const gauge of gauges) {
    const candidate = Math.hypot(gauge.lat - latitude, gauge.lon - longitude);
    if (candidate < distance) {
      nearest = gauge;
      distance = candidate;
    }
  }
  return distance <= 0.5 ? nearest : null;
}

/** Assembles only accepted database publications for the AI evidence boundary. */
export async function assembleRegionalContext(
  lat: number,
  lon: number
): Promise<RegionalContextResult> {
  const bbox = `${Math.max(-180, lon - 0.25)},${Math.max(
    -90,
    lat - 0.25
  )},${Math.min(180, lon + 0.25)},${Math.min(90, lat + 0.25)}`;
  const [strategy, drought, gauges, carbon] = await Promise.allSettled([
    getStrategyRecommendations(lat, lon),
    getPublishedDroughtClassification(),
    getPublishedStreamflowGauges(bbox),
    getInterventionSuitability(lat, lon),
  ]);

  const droughtValue = drought.status === "fulfilled" ? drought.value : null;
  const gaugeValues = gauges.status === "fulfilled" ? gauges.value : [];
  const carbonValue = carbon.status === "fulfilled" ? carbon.value : null;
  const strategyValues = strategy.status === "fulfilled" ? strategy.value : [];
  const dataFreshness: Record<string, string> = {
    strategyRecommendations:
      strategyValues.length > 0 ? "published_revision_required" : "unavailable",
    soilProperties: "unavailable",
    drought:
      droughtValue?.availability === "published" && droughtValue.observedAt
        ? droughtValue.observedAt
        : "unavailable",
    streamflow:
      gaugeValues
        .map((gauge) => gauge.updatedAt)
        .filter((value) => Number.isFinite(Date.parse(value)))
        .sort()
        .at(-1) ?? "unavailable",
    mtbsPerimeters: "unavailable",
    carbonPotential:
      carbonValue?.availability === "published" ? "published_revision_required" : "unavailable",
  };

  const hasPublishedEvidence = Object.entries(dataFreshness).some(
    ([source, freshness]) =>
      isRegionalEvidenceSource(source) &&
      regionalEvidenceFreshnessState(source, freshness) === "available"
  );
  if (!hasPublishedEvidence) {
    throw new Error("No versioned regional evidence is published");
  }

  const payload: RegionalContextPayload = {
    location: { lat, lon, geohash: `${lat.toFixed(2)}_${lon.toFixed(2)}` },
    strategyRecommendations: strategyValues.length > 0 ? strategyValues : null,
    soilProperties: null,
    waterScarcity:
      droughtValue?.availability === "published" || gaugeValues.length > 0
        ? {
            droughtClass: droughtValue
              ? droughtClassAtPoint(droughtValue, lat, lon)
              : null,
            nearestGauge: nearestGauge(gaugeValues, lat, lon),
          }
        : null,
    mtbsPerimeters: null,
    carbonPotential:
      carbonValue?.availability === "published" ? carbonValue : null,
  };
  return { payload, dataFreshness, cacheHit: false };
}
