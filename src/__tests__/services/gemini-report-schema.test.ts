import { describe, expect, it } from "vitest";
import { geminiReportSchema } from "@/lib/server/services/gemini-report-schema";
import { remediationReportSchema, REMEDIATION_REPORT_JSON_SCHEMA } from "@/lib/server/services/remediation-report";

describe("Gemini report schema projection", () => {
  it("moves decoding length/count bounds into instructions without mutating structural constraints or the canonical schema", () => {
    const before = JSON.stringify(REMEDIATION_REPORT_JSON_SCHEMA);
    const projected = geminiReportSchema(REMEDIATION_REPORT_JSON_SCHEMA);
    expect(JSON.stringify(REMEDIATION_REPORT_JSON_SCHEMA)).toBe(before);
    expect(projected).not.toHaveProperty("properties.observations.maxItems");
    expect(projected).not.toHaveProperty("properties.observations.items.properties.statement.maxLength");
    expect(projected).not.toHaveProperty("properties.observations.items.properties.statement.minLength");
    expect(projected).toHaveProperty("properties.observations.description", "Maximum item count: 12.");
    expect(projected).toHaveProperty("properties.observations.items.properties.statement.description", "Minimum string length: 1. Maximum string length: 500.");
    expect(projected).toMatchObject({
      type: "object", additionalProperties: false,
      required: ["riskSummary", "observations", "remediation", "professionalConsultation"],
      properties: { observations: { type: "array", items: { type: "object", additionalProperties: false, required: ["statement", "evidenceOrigin"], properties: { evidenceOrigin: { enum: ["warehouse", "web", "model_inference"] } } } } },
    });
  });

  it("keeps over-limit, empty and extra-field reports invalid under the canonical runtime contract", () => {
    const valid = { riskSummary: { level: "low", headline: "Limited evidence", factors: [], evidenceOrigin: "model_inference", evidenceSources: [] }, observations: [], remediation: [], professionalConsultation: "Consult a professional." };
    geminiReportSchema(REMEDIATION_REPORT_JSON_SCHEMA);
    expect(remediationReportSchema.safeParse(valid).success).toBe(true);
    expect(remediationReportSchema.safeParse({ ...valid, observations: Array.from({ length: 13 }, () => ({ statement: "Observation", evidenceOrigin: "model_inference" })) }).success).toBe(false);
    expect(remediationReportSchema.safeParse({ ...valid, observations: [{ statement: "x".repeat(501), evidenceOrigin: "model_inference" }] }).success).toBe(false);
    expect(remediationReportSchema.safeParse({ ...valid, professionalConsultation: "" }).success).toBe(false);
    expect(remediationReportSchema.safeParse({ ...valid, unexpected: true }).success).toBe(false);
  });
});
