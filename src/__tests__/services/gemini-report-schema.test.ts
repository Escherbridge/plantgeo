import { describe, expect, it } from "vitest";
import { geminiReportSchema } from "@/lib/server/services/gemini-report-schema";
import { remediationReportSchema, REMEDIATION_REPORT_JSON_SCHEMA, reportSchemaForCitations } from "@/lib/server/services/remediation-report";

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

  it('keeps complete citation branches and forbidden inference fields while simplifying large bounds', () => {
    const scoped = reportSchemaForCitations({ payloadSources: [], measurementReads: [{
      evidenceSource: 'vegetation', evidenceReadId: 'local-1', stage: 'local', selectedDate: '2026-09-09',
      rangeStart: undefined, rangeEnd: undefined, servedDates: undefined, observedDates: undefined,
    }] });
    const projected = geminiReportSchema(scoped);
    expect(projected).toHaveProperty('type', 'object');
    expect(projected).toHaveProperty('properties.observations.minItems', 1);
    expect(projected).toHaveProperty('properties.observations.description', expect.stringContaining('Measurements were returned'));
    for (const { path, required } of [
      { path: 'properties.riskSummary', required: ['level', 'headline', 'factors', 'evidenceOrigin', 'evidenceSources'] },
      { path: 'properties.observations.items', required: ['statement', 'evidenceOrigin'] },
      { path: 'properties.remediation.items', required: ['strategy', 'title', 'rationale', 'timeframe', 'confidence', 'consultProfessionals', 'evidenceOrigin'] },
    ]) {
      expect(projected).not.toHaveProperty(`${path}.properties`);
      for (const index of [0, 1]) {
        expect(projected).toHaveProperty(`${path}.anyOf.${index}.type`, 'object');
        expect(projected).toHaveProperty(`${path}.anyOf.${index}.additionalProperties`, false);
        const warehouseRequired = [...required, ...(path === 'properties.riskSummary' ? [] : ['evidenceSource']), 'evidenceReadIds'];
        expect(projected).toHaveProperty(`${path}.anyOf.${index}.required`, index === 0 ? warehouseRequired : required);
        for (const field of required) expect(projected).toHaveProperty(`${path}.anyOf.${index}.properties.${field}`);
      }
      expect(projected).toHaveProperty(`${path}.anyOf.0.properties.evidenceReadIds.minItems`, 1);
      expect(projected).not.toHaveProperty(`${path}.anyOf.0.properties.evidenceReadIds.maxItems`);
      expect(projected).toHaveProperty(`${path}.anyOf.0.properties.evidenceReadIds.description`, expect.stringContaining('Maximum item count: 8.'));
      expect(projected).not.toHaveProperty(`${path}.anyOf.1.properties.evidenceReadIds`);
      expect(projected).toHaveProperty(`${path}.anyOf.1.properties.evidenceOrigin.enum`, ['web', 'model_inference']);
    }
  });
});
