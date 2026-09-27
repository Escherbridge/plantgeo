import { describe, expect, it } from "vitest";
import { soilAiEvidence } from "@/lib/server/services/soil-ai-evidence";
import { soilEstimateFromMapped } from "@/lib/server/services/soilgrids";
import type { SoilGridsMapped } from "@/lib/server/services/site-brief";

/** CONTRACT C5.1 worked-example mapped integers. */
const MAPPED: SoilGridsMapped = {
  "0-5cm": { phh2o: 57, soc: 243, nitrogen: 190, bdod: 121, cec: 182, ocd: 380, clay: 189, sand: 371, silt: 440, cfvo: 98 },
  "5-15cm": { phh2o: 57, soc: 210, nitrogen: 160, bdod: 127, cec: 170, ocd: 340, clay: 195, sand: 380, silt: 425, cfvo: 102 },
  "15-30cm": { phh2o: 58, soc: 180, nitrogen: 130, bdod: 131, cec: 160, ocd: 300, clay: 201, sand: 390, silt: 409, cfvo: 106 },
};

describe("SoilGrids evidence units", () => {
  it("keeps normalized concentrations and pairs every AI value with its actual unit", () => {
    expect(soilAiEvidence({ ph: 6.6, organicCarbon: 61.9, nitrogen: 5.12, bulkDensity: 1.25, cec: 14, ocd: 3.2 })).toEqual({
      depth: "0–5 cm",
      method: "SoilGrids model predictions; not a local soil sample",
      basis: "model_estimate",
      label: "SoilGrids v2.0 250 m model estimate, 0-5 cm",
      ph: { value: 6.6, unit: "pH (water), dimensionless" },
      organicCarbon: { value: 61.9, unit: "g/kg" },
      nitrogen: { value: 5.12, unit: "g/kg" },
      bulkDensity: { value: 1.25, unit: "g/cm³" },
      cec: { value: 14, unit: "cmol(c)/kg" },
      ocd: { value: 3.2, unit: "kg/m³" },
    });
  });

  it("carries the lane's own label, distance, release and labelled topsoil summary", () => {
    const evidence = soilAiEvidence(soilEstimateFromMapped(MAPPED, "soilgrids-v2.0/2020-06-02", 140));
    expect(evidence).toMatchObject({
      basis: "model_estimate",
      label: "SoilGrids v2.0 250 m model estimate, 0-5 cm, cell centre 140 m away (release soilgrids-v2.0/2020-06-02)",
      cellCentreDistanceM: 140,
      releaseId: "soilgrids-v2.0/2020-06-02",
      ph: { value: 5.7 },
      organicCarbon: { value: 24.3, unit: "g/kg" },
      topsoil0to30cm: {
        label: "SoilGrids v2.0 250 m model estimate, 0-30 cm (thickness-weighted)",
        texture: "loam", reaction: "moderately acid", organicCarbonBand: "high organic carbon",
        ph: { value: 5.8 }, organicCarbon: { value: 2, unit: "% (mass)" }, clay: { value: 19.7 },
      },
    });
    expect(JSON.stringify(evidence)).not.toMatch(/measured|observed/i);
  });

  it("keeps missing soil evidence missing", () => {
    expect(soilAiEvidence(null)).toBeNull();
  });
});
