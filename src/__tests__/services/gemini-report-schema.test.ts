import { describe, expect, it } from "vitest";
import { geminiReportSchema } from "@/lib/server/services/gemini-report-schema";
import { remediationReportSchema, REMEDIATION_REPORT_JSON_SCHEMA, reportSchemaForCitations } from "@/lib/server/services/remediation-report";
import { buildRegionalMeasurementFacts } from '@/lib/server/services/regional-measurement-facts';

describe("Gemini report schema projection", () => {
  it('preserves exact fact-ID selection without a free warehouse prose branch', () => {
    const facts = buildRegionalMeasurementFacts([{ id: 'local-1', source: 'vegetation', result: { features: [{ observed_day: '2026-09-09', properties: { ndvi: 0.3558 } }] } }]).facts;
    const projected = geminiReportSchema(reportSchemaForCitations({ payloadSources: [], measurementReads: [] }, facts));
    expect(projected).toHaveProperty('properties.observations.minItems', 1);
    expect(projected).toHaveProperty('properties.observations.items.anyOf.0', {
      type: 'object', additionalProperties: false,
      required: ['evidenceOrigin', 'measurementFactId'],
      properties: {
        evidenceOrigin: { type: 'string', enum: ['warehouse'] },
        measurementFactId: { type: 'string', enum: [facts[0].id], description: expect.stringContaining('server supplies its exact statement') },
      },
    });
    expect(projected).not.toHaveProperty('properties.observations.items.anyOf.1.properties.evidenceSource');
    expect(projected).not.toHaveProperty('properties.observations.items.anyOf.1.properties.evidenceReadIds');
  });

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
    expect(projected).toHaveProperty('properties.riskSummary.properties.evidenceOrigin.enum', ['model_inference']);
    expect(projected).toHaveProperty('properties.riskSummary.properties.evidenceSources.maxItems', 0);
    expect(projected).not.toHaveProperty('properties.riskSummary.properties.evidenceReadIds');
    expect(projected).toHaveProperty('properties.remediation.items.properties.evidenceOrigin.enum', ['web', 'model_inference']);
    expect(projected).not.toHaveProperty('properties.remediation.items.properties.evidenceSource');
    expect(projected).not.toHaveProperty('properties.remediation.items.properties.evidenceReadIds');
    expect(projected).toHaveProperty('properties.remediation.items.required', ['strategy', 'title', 'rationale', 'timeframe', 'confidence', 'consultProfessionals', 'evidenceOrigin']);
    for (const { path, required } of [
      { path: 'properties.observations.items', required: ['statement', 'evidenceOrigin'] },
    ]) {
      expect(projected).not.toHaveProperty(`${path}.properties`);
      for (const index of [0, 1]) {
        expect(projected).toHaveProperty(`${path}.anyOf.${index}.type`, 'object');
        expect(projected).toHaveProperty(`${path}.anyOf.${index}.additionalProperties`, false);
        const warehouseRequired = [...required, 'evidenceSource', 'evidenceReadIds'];
        expect(projected).toHaveProperty(`${path}.anyOf.${index}.required`, index === 0 ? warehouseRequired : required);
        for (const field of required) expect(projected).toHaveProperty(`${path}.anyOf.${index}.properties.${field}`);
      }
      expect(projected).toHaveProperty(`${path}.anyOf.0.properties.evidenceReadIds.minItems`, 1);
      expect(projected).not.toHaveProperty(`${path}.anyOf.0.properties.evidenceReadIds.maxItems`);
      expect(projected).toHaveProperty(`${path}.anyOf.0.properties.evidenceReadIds.description`, expect.stringContaining('Maximum item count: 8.'));
      expect(projected).not.toHaveProperty(`${path}.anyOf.1.properties.evidenceReadIds`);
      expect(projected).not.toHaveProperty(`${path}.anyOf.1.properties.evidenceSource`);
      expect(projected).toHaveProperty(`${path}.anyOf.1.properties.evidenceOrigin.enum`, ['web', 'model_inference']);
      expect(projected).toHaveProperty(`${path}.anyOf.0.properties.statement.description`, expect.stringContaining('Describe only measurements returned by vegetation'));
    }
  });
});
