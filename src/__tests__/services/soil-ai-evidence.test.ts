import { describe, expect, it } from "vitest";
import { soilAiEvidence } from "@/lib/server/services/soil-ai-evidence";

describe("SoilGrids evidence units", () => {
  it("keeps normalized concentrations and pairs every AI value with its actual unit", () => {
    expect(soilAiEvidence({ ph: 6.6, organicCarbon: 61.9, nitrogen: 5.12, bulkDensity: 1.25, cec: 14, ocd: 3.2 })).toEqual({
      depth: "0–5 cm",
      method: "SoilGrids model predictions; not a local soil sample",
      ph: { value: 6.6, unit: "pH (water), dimensionless" },
      organicCarbon: { value: 61.9, unit: "g/kg" },
      nitrogen: { value: 5.12, unit: "g/kg" },
      bulkDensity: { value: 1.25, unit: "g/cm³" },
      cec: { value: 14, unit: "cmol(c)/kg" },
      ocd: { value: 3.2, unit: "kg/m³" },
    });
  });

  it("keeps missing soil evidence missing", () => {
    expect(soilAiEvidence(null)).toBeNull();
  });
});
