import { describe, expect, it } from "vitest";
import { geminiEvidenceSchema, geminiReportSchema } from "@/lib/server/services/gemini-report-schema";
import { labelSoilModelEstimates, remediationReportSchema, REMEDIATION_REPORT_JSON_SCHEMA, reportSchemaForCitations, SOIL_MODEL_ESTIMATE_SENTENCE, SOIL_MODEL_ESTIMATE_SUFFIX } from "@/lib/server/services/remediation-report";
import { buildRegionalMeasurementFacts } from '@/lib/server/services/regional-measurement-facts';

describe("Gemini evidence-tool projection (AGENTS.md §gemini-forced-call-states, afternoon)", () => {
  const flattened = (items: Record<string, unknown>, type: unknown = "array") =>
    (geminiEvidenceSchema({ type: "object", properties: { pick: { type, items, description: "Pick some." } } }).properties as Record<string, { items: unknown; description: string }>).pick;

  it.each([
    ["a plain string enum", { type: "string", enum: ["a", "b"] }, "array", { type: "string" }, "Pick some. Allowed values: a, b."],
    ["an untyped string enum", { enum: ["a", "b"] }, "array", { type: "string" }, "Pick some. Allowed values: a, b."],
    ["an anyOf of enums", { anyOf: [{ enum: ["a"] }, { enum: ["b"] }] }, "array", { type: "string" }, "Pick some. Allowed values: a, b."],
    ["a nullable array", { type: "string", enum: ["a"] }, ["array", "null"], { type: "string" }, "Pick some. Allowed values: a."],
    ["a numeric enum", { type: "integer", enum: [0, 5] }, "array", { type: "integer" }, "Pick some. Allowed values: 0, 5."],
  ])("drops the enum loop from %s and names its values", (_label, items, type, expectedItems, expectedDescription) => {
    const pick = flattened(items, type);
    expect(pick.items).toEqual(expectedItems);
    expect(pick.description).toBe(expectedDescription);
  });

  it("leaves arrays of objects, scalar enums and the input schema alone", () => {
    const schema = { type: "object", properties: {
      level: { type: "string", enum: ["low", "high"] },
      rows: { type: "array", items: { type: "object", properties: { kind: { type: "string", enum: ["x"] } } } },
    } };
    const before = structuredClone(schema);
    expect(geminiEvidenceSchema(schema)).toEqual(schema);
    expect(schema).toEqual(before);
  });
});

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
      properties: { observations: { type: "array", items: { type: "object", additionalProperties: false, required: ["statement", "evidenceOrigin"], properties: { evidenceOrigin: { enum: ["warehouse", "web", "literature", "model_inference"] } } } } },
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
    // Literature is offered only once a strategy-knowledge tool answered, and survives the projection.
    const literature = geminiReportSchema(reportSchemaForCitations({ payloadSources: [], measurementReads: [] }, [], { literatureAnswered: true }));
    expect(literature).toHaveProperty('properties.remediation.items.properties.evidenceOrigin.enum', ['web', 'model_inference', 'literature']);
    expect(literature).toHaveProperty('properties.remediation.items.properties.evidenceSource.enum', ['strategy-knowledge']);
    expect(literature).not.toHaveProperty('properties.remediation.items.properties.evidenceReadIds');
    // Record ids are model-supplied and survive the projection; citations and grounding notes are
    // server-written, so no projection ever offers them to the model.
    expect(projected).not.toHaveProperty('properties.remediation.items.properties.literatureRecordIds');
    const citable = geminiReportSchema(reportSchemaForCitations({ payloadSources: [], measurementReads: [] }, [], {
      literatureAnswered: true, literatureRecordIds: ['sk-finding-F8101', 'reduced-tillage-no-till'],
    }));
    expect(citable).toHaveProperty('properties.remediation.items.properties.literatureRecordIds.items.enum', ['sk-finding-F8101', 'reduced-tillage-no-till']);
    expect(citable).toHaveProperty('properties.remediation.items.properties.literatureRecordIds.description', expect.stringContaining('Maximum item count: 8.'));
    expect(citable).not.toHaveProperty('properties.remediation.items.properties.literatureRecordIds.maxItems');
    for (const schema of [projected, literature, citable, geminiReportSchema(REMEDIATION_REPORT_JSON_SCHEMA)]) {
      expect(JSON.stringify(schema)).not.toContain('literatureCitations');
      expect(JSON.stringify(schema)).not.toContain('groundingNote');
    }
    expect(projected).toHaveProperty('properties.remediation.items.required',['strategy', 'title', 'rationale', 'timeframe', 'confidence', 'consultProfessionals', 'evidenceOrigin']);
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

/**
 * Interim O2 rule (DESIGN §5.7): until the panel's h7 fix lands, `soilProperties` is citable only
 * where its badge already reads "Published estimate" (observations), never on remediation items or
 * riskSummary, whose render paths badge by origin alone and would say "Observed data".
 */
describe("SoilGrids model estimates in the report contract", () => {
  const valid = { riskSummary: { level: "low", headline: "Limited evidence", factors: [], evidenceOrigin: "model_inference", evidenceSources: [] }, observations: [], remediation: [], professionalConsultation: "Consult a professional." };
  const item = {
    strategy: "cover_cropping", title: "Screen cover crops", rationale: "Soil pH suits several species.",
    timeframe: "short_term", confidence: "low", consultProfessionals: ["soil_scientist"],
  };

  it("keeps soilProperties out of the remediation and riskSummary citation enums only", () => {
    const properties = REMEDIATION_REPORT_JSON_SCHEMA.properties as Record<string, { properties?: Record<string, { enum?: string[]; items?: { enum?: string[]; properties?: Record<string, { enum?: string[] }> } }>; items?: { properties?: Record<string, { enum?: string[] }> } }>;
    expect(properties.remediation.items?.properties?.evidenceSource.enum).not.toContain("soilProperties");
    expect(properties.riskSummary.properties?.evidenceSources.items?.enum).not.toContain("soilProperties");
    expect(properties.observations.items?.properties?.evidenceSource.enum).toContain("soilProperties");
    expect(remediationReportSchema.safeParse({ ...valid, remediation: [{ ...item, evidenceOrigin: "warehouse", evidenceSource: "soilProperties" }] }).success).toBe(false);
    expect(remediationReportSchema.safeParse({ ...valid, riskSummary: { ...valid.riskSummary, evidenceOrigin: "warehouse", evidenceSources: ["soilProperties"] } }).success).toBe(false);
    expect(remediationReportSchema.safeParse({ ...valid, observations: [{ statement: "SoilGrids model estimate pH 5.8.", evidenceOrigin: "warehouse", evidenceSource: "soilProperties" }] }).success).toBe(true);
  });

  it("appends the model-estimate sentence to an unlabelled soil number, deterministically and once", () => {
    const report = remediationReportSchema.parse({ ...valid, observations: [
      { statement: "Topsoil pH is 5.8.", evidenceOrigin: "warehouse", evidenceSource: "soilProperties" },
      { statement: "Topsoil pH is 5.8, a SoilGrids model estimate.", evidenceOrigin: "warehouse", evidenceSource: "soilProperties" },
      { statement: "Streamflow was 14 cfs.", evidenceOrigin: "warehouse", evidenceSource: "streamflow" },
      { statement: "x".repeat(495) + " 5.8", evidenceOrigin: "warehouse", evidenceSource: "soilProperties" },
    ] });
    const labelled = labelSoilModelEstimates(report, { soilAvailable: true });
    expect(labelled.observations[0].statement).toBe(`Topsoil pH is 5.8.${SOIL_MODEL_ESTIMATE_SENTENCE}`);
    expect(labelled.observations[1].statement).toBe(report.observations[1].statement);
    expect(labelled.observations[2].statement).toBe("Streamflow was 14 cfs.");
    expect(labelled.observations[3].statement.length).toBeLessThanOrEqual(500);
    expect(labelled.observations[3].statement.endsWith(SOIL_MODEL_ESTIMATE_SENTENCE)).toBe(true);
    expect(labelSoilModelEstimates(labelled, { soilAvailable: true })).toEqual(labelled);
    expect(remediationReportSchema.safeParse(labelled).success).toBe(true);
  });

  /**
   * Review M2: the live path runs with measurement facts on, so the model can never cite
   * soilProperties -- soil numbers arrive in model_inference statements, rationales, risk factors and
   * the headline. The rule keys on the text whenever soil reached the model.
   */
  it("labels soil numbers in the live report shape, wherever the model put them", () => {
    const live = remediationReportSchema.parse({
      riskSummary: {
        level: "moderate", headline: "Acid topsoil (pH 5.8) under moderate drought",
        factors: ["topsoil pH 5.8", "D1 drought for 3 weeks", "clay 19.7% loam"],
        evidenceOrigin: "model_inference", evidenceSources: [],
      },
      observations: [
        { statement: "The loam topsoil has pH 5.8 and 2.0% SOC, which favours acid-tolerant cover crops.", evidenceOrigin: "model_inference" },
        { statement: "Drought reached D1 in 3 of the last 4 weeks.", evidenceOrigin: "model_inference" },
        { statement: "Soil moisture was 23% on 2026-09-20.", evidenceOrigin: "model_inference" },
      ],
      remediation: [{
        strategy: "cover_cropping", title: "Acid-tolerant cover", rationale: "At pH 5.8 with 38.4% sand, cereal rye establishes well.",
        timeframe: "short_term", confidence: "low", consultProfessionals: ["soil_scientist"], evidenceOrigin: "model_inference",
      }],
      professionalConsultation: "Consult a soil scientist.",
    });
    const labelled = labelSoilModelEstimates(live, { soilAvailable: true });
    expect(labelled.observations[0].statement.endsWith(SOIL_MODEL_ESTIMATE_SENTENCE)).toBe(true);
    expect(labelled.observations[1].statement).toBe(live.observations[1].statement);
    // Soil moisture is not a SoilGrids property: never relabelled as a model estimate.
    expect(labelled.observations[2].statement).toBe(live.observations[2].statement);
    expect(labelled.remediation[0].rationale.endsWith(SOIL_MODEL_ESTIMATE_SENTENCE)).toBe(true);
    expect(labelled.riskSummary.headline).toBe(`Acid topsoil (pH 5.8) under moderate drought${SOIL_MODEL_ESTIMATE_SUFFIX}`);
    expect(labelled.riskSummary.factors).toEqual([`topsoil pH 5.8${SOIL_MODEL_ESTIMATE_SUFFIX}`, "D1 drought for 3 weeks", `clay 19.7% loam${SOIL_MODEL_ESTIMATE_SUFFIX}`]);
    expect(remediationReportSchema.safeParse(labelled).success).toBe(true);
    expect(labelSoilModelEstimates(labelled, { soilAvailable: true })).toEqual(labelled);
  });

  it("keeps every field within its limit when it appends", () => {
    const long = remediationReportSchema.parse({
      riskSummary: { level: "low", headline: `${"x".repeat(292)} pH 5.8`, factors: [`${"y".repeat(232)} pH 5.8`], evidenceOrigin: "model_inference", evidenceSources: [] },
      observations: [], professionalConsultation: "Consult a professional.",
      remediation: [{ strategy: "cover_cropping", title: "t", rationale: `${"z".repeat(890)} pH 5.8`, timeframe: "short_term", confidence: "low", consultProfessionals: ["soil_scientist"], evidenceOrigin: "model_inference" }],
    });
    const labelled = labelSoilModelEstimates(long, { soilAvailable: true });
    expect(labelled.riskSummary.headline.length).toBeLessThanOrEqual(300);
    expect(labelled.riskSummary.factors[0].length).toBeLessThanOrEqual(240);
    expect(labelled.remediation[0].rationale.length).toBeLessThanOrEqual(900);
    expect(remediationReportSchema.safeParse(labelled).success).toBe(true);
  });

  it("leaves soil terms alone when no soil reached the model, but still labels a soilProperties citation", () => {
    const report = remediationReportSchema.parse({ ...valid, observations: [
      { statement: "Topsoil pH is 5.8.", evidenceOrigin: "model_inference" },
      { statement: "Surface pH is 5.7.", evidenceOrigin: "warehouse", evidenceSource: "soilProperties" },
    ] });
    const labelled = labelSoilModelEstimates(report, { soilAvailable: false });
    expect(labelled.observations[0].statement).toBe("Topsoil pH is 5.8.");
    expect(labelled.observations[1].statement).toBe(`Surface pH is 5.7.${SOIL_MODEL_ESTIMATE_SENTENCE}`);
  });
});
