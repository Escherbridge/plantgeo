import type { SoilProperties } from "@/lib/server/services/soilgrids";
import type {
  CarbonPotential,
  InterventionType,
} from "@/lib/environmental/intervention";

export type {
  CarbonPotential,
  InterventionType,
} from "@/lib/environmental/intervention";

interface PublishedIntervention {
  suitabilityScore: number;
  carbonPotential: CarbonPotential;
  rationale: string;
}

export interface InterventionSuitability {
  availability: "unavailable" | "published";
  reason: string | null;
  revision: string | null;
  lat: number;
  lon: number;
  soilProps: SoilProperties | null;
  erosionRisk: number | null;
  erosionClass: string | null;
  interventions: Partial<Record<InterventionType, PublishedIntervention>>;
}

/** Refuses to manufacture carbon or intervention effects from uncited heuristics. */
export async function getInterventionSuitability(
  lat: number,
  lon: number
): Promise<InterventionSuitability> {
  return {
    availability: "unavailable",
    reason:
      "No validated warehouse-backed intervention-effect release is published",
    revision: null,
    lat,
    lon,
    soilProps: null,
    erosionRisk: null,
    erosionClass: null,
    interventions: {},
  };
}
