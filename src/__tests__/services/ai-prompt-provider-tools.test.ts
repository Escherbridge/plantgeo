import { afterEach, describe, expect, it, vi } from 'vitest';
import type { RegionalContextPayload, TemporalContext } from '@/lib/server/services/regional-context';
import type { RegionalMeasurementFact } from '@/lib/server/services/regional-measurement-facts';

// Provider-facing tool schemas and their complexity budget; see services/AGENTS.md §provider-tool-budget.
const mocks = vi.hoisted(() => ({ load: vi.fn(), call: vi.fn(), completionStream: vi.fn() }));
vi.mock('@/lib/server/db', () => ({ db: {} }));
vi.mock('@/lib/server/services/regional-evidence-tools', async (importOriginal) => ({
  // Real `RegionalEvidenceArgumentError`: ai-prompt.ts checks it with `instanceof`.
  ...await importOriginal<typeof import('@/lib/server/services/regional-evidence-tools')>(),
  loadRegionalEvidenceTools: mocks.load, callRegionalEvidenceTool: mocks.call,
}));
vi.mock('openai', () => ({
  default: class MockOpenAI {
    chat = { completions: { stream: mocks.completionStream } };
  },
}));

import { bindProviderEvidenceArguments, DEFAULT_MODEL, providerFunctionTools, REPORT_TOOL, SEARCH_TOOL, SERVER_BOUND_EVIDENCE_ARGUMENTS, streamRegionalIntelligence } from '@/lib/server/services/ai-prompt';
import { geminiEvidenceSchema, geminiReportSchema } from '@/lib/server/services/gemini-report-schema';
import { landContextTools } from '@/lib/server/services/land-context-tools';
import { bindRegionalEvidenceArguments, SERVER_OWNED_LITERATURE_ARGUMENTS } from '@/lib/server/services/regional-analysis-workflow';
import { groundLiteratureClaims, normalizeProviderReport, pairLiteratureProvenance, remediationReportSchema, reportSchemaForCitations, resolveProviderMeasurementReport } from '@/lib/server/services/remediation-report';
import * as webEvidence from '@/lib/server/services/web-evidence';
import { isStrategyKnowledgeTool } from '@/lib/regional-intelligence';
import agriCatalogueFixture from './agri-tool-catalogue-56467bd4.fixture.json';
import agriCurrentCatalogueFixture from './agri-tool-catalogue-current.fixture.json';

type CatalogueEntry = { type: 'function'; function: { name: string; description: string; parameters: Record<string, unknown> } };
type ProviderTool = { type: string; function: { name: string; parameters: Record<string, unknown> } };

/** The SDK types `parameters` as optional; every tool this module sends carries one. */
const asProviderTools = (tools: unknown): ProviderTool[] => tools as ProviderTool[];

/** The agri catalogue live during the 2026-09-28 incident: `agent/llm.py::tool_schemas` at 56467bd4. */
const AGRI_CATALOGUE_56467BD4 = agriCatalogueFixture as unknown as CatalogueEntry[];

/** The catalogue `GET /agent-tools/` publishes NOW with SOIL_PROPERTIES_READS_ENABLED=true (production). */
const AGRI_CATALOGUE_CURRENT = agriCurrentCatalogueFixture as unknown as CatalogueEntry[];

/**
 * The round-1 shape of d060dd2b, the last catalogue Gemini accepted: 112 properties, 208 enum
 * values, 102 constraints with the 20-fact report below. Incident shape (web 8b4b0a88 + agri
 * 56467bd4) was 117 / 221 / 102 and was rejected `schema_too_complex`.
 */
const KNOWN_GOOD_ROUND_ONE_BUDGET = { properties: 112, enumValues: 208, constraints: 102 } as const;

const CONSTRAINT_KEYWORDS = new Set([
  'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'minLength', 'maxLength', 'pattern',
  'format', 'minItems', 'maxItems', 'multipleOf', 'uniqueItems', 'const',
]);

interface SchemaComplexity { properties: number; enumValues: number; constraints: number }

/** Counts declared properties, enum values and bound keywords; the diagnosis's `metrics.py` walk. */
function addSchemaComplexity(node: unknown, total: SchemaComplexity): SchemaComplexity {
  if (Array.isArray(node)) {
    for (const item of node) addSchemaComplexity(item, total);
    return total;
  }
  if (node === null || typeof node !== 'object') return total;
  for (const [key, value] of Object.entries(node)) {
    if (key === 'properties' && value !== null && typeof value === 'object' && !Array.isArray(value)) {
      total.properties += Object.keys(value).length;
      for (const child of Object.values(value)) addSchemaComplexity(child, total);
      continue;
    }
    if (key === 'enum' && Array.isArray(value)) total.enumValues += value.length;
    if (CONSTRAINT_KEYWORDS.has(key)) total.constraints += 1;
    if (value !== null && typeof value === 'object') addSchemaComplexity(value, total);
  }
  return total;
}

function totalComplexity(schemas: readonly unknown[]): SchemaComplexity {
  const total = { properties: 0, enumValues: 0, constraints: 0 };
  for (const schema of schemas) addSchemaComplexity(schema, total);
  return total;
}

function agriTools() {
  return AGRI_CATALOGUE_56467BD4.map(({ function: tool }) => ({
    name: tool.name, description: tool.description, input_schema: structuredClone(tool.parameters),
  }));
}

function agriTool(name: string) {
  const tool = agriTools().find((candidate) => candidate.name === name);
  if (!tool) throw new Error(`The fixture has no ${name} tool.`);
  return tool;
}

function catalogueParameters(name: string): Record<string, unknown> {
  const entry = AGRI_CATALOGUE_56467BD4.find((tool) => tool.function.name === name);
  if (!entry) throw new Error(`The fixture has no ${name} tool.`);
  return entry.function.parameters;
}

/** Twenty synthetic facts, the pool the diagnosis measured every commit at. */
const TWENTY_FACTS: RegionalMeasurementFact[] = Array.from({ length: 20 }, (_, index) => ({
  id: `fact-${String(index).padStart(3, '0')}-abcdef`,
  source: 'vegetation',
  statement: `Vegetation cover was 0.${index + 10} on 2026-09-09.`,
  evidenceReadIds: [`local-${index + 1}`] as [string],
}));

const roundOneReportSchema = () => reportSchemaForCitations(
  { payloadSources: [], measurementReads: [] }, TWENTY_FACTS, { literatureAnswered: false, literatureRecordIds: [] },
);

/** Round 1 as production builds it: web search, the report, then the agri and land-context catalogue. */
const incidentToolSet = () => [SEARCH_TOOL, REPORT_TOOL, ...agriTools(), ...landContextTools()];

const payload: RegionalContextPayload = {
  location: { lat: 43.6, lon: -116.2, geohash: '43.60_-116.20' },
  strategyRecommendations: null, strategyContext: [], communityProposals: [], soilProperties: null,
  waterScarcity: null, weather: null, fireDetections: null, firePerimeters: null, mtbsPerimeters: null, carbonPotential: null,
};
const temporal: TemporalContext = {
  serverCurrentDate: '2026-09-28', viewedLayersUnreported: true, readings: [], viewedDates: [], sourcesServedAsOfLatest: [],
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  mocks.load.mockReset();
  mocks.call.mockReset();
  mocks.completionStream.mockReset();
});

describe('provider tool complexity budget (2026-09-28 schema_too_complex incident)', () => {
  it('keeps the round-1 tool set Gemini sees within the last accepted shape', () => {
    const tools = asProviderTools(providerFunctionTools(incidentToolSet(), roundOneReportSchema(), DEFAULT_MODEL));
    expect(tools).toHaveLength(20);
    const total = totalComplexity(tools.map((tool) => tool.function.parameters));
    expect(total.properties).toBeLessThanOrEqual(KNOWN_GOOD_ROUND_ONE_BUDGET.properties);
    expect(total.enumValues).toBeLessThanOrEqual(KNOWN_GOOD_ROUND_ONE_BUDGET.enumValues);
    expect(total.constraints).toBeLessThanOrEqual(KNOWN_GOOD_ROUND_ONE_BUDGET.constraints);
  });

  // The CURRENT published catalogue (soil flag on, drought/fire history re-published 2026-10-04),
  // as `GET /agent-tools/` serves it. agri `test_agent_closest_datapoint.py::
  // test_the_web_current_catalogue_fixture_is_the_published_catalogue` fails when it drifts.
  it('keeps the CURRENT published catalogue within the last accepted shape', () => {
    const current = (AGRI_CATALOGUE_CURRENT as CatalogueEntry[]).map(({ function: tool }) => ({
      name: tool.name, description: tool.description, input_schema: structuredClone(tool.parameters),
    }));
    expect(current.map((tool) => tool.name)).toEqual(expect.arrayContaining([
      'drought_history_at_point', 'fire_history_near_point', 'soil_properties_at_point',
    ]));
    const tools = asProviderTools(providerFunctionTools(
      [SEARCH_TOOL, REPORT_TOOL, ...current, ...landContextTools()], roundOneReportSchema(), DEFAULT_MODEL,
    ));
    const total = totalComplexity(tools.map((tool) => tool.function.parameters));
    expect(total.properties).toBeLessThanOrEqual(KNOWN_GOOD_ROUND_ONE_BUDGET.properties);
    expect(total.enumValues).toBeLessThanOrEqual(KNOWN_GOOD_ROUND_ONE_BUDGET.enumValues);
    expect(total.constraints).toBeLessThanOrEqual(KNOWN_GOOD_ROUND_ONE_BUDGET.constraints);
    expect(tools.flatMap((tool) => tool.function.name === REPORT_TOOL.name ? [] : enumArrays(tool.function.parameters))).toEqual([]);
    // Live since fec24ed3 (2026-09-13); any other property-less OBJECT is a shape Gemini has not been shown.
    expect(tools.flatMap((tool) => emptyObjectProperties(tool.function.parameters, tool.function.name))).toEqual(['draft_land_inquiry_text.contact']);
  });

  it('would have failed on the incident shape, so the budget is a real tripwire', () => {
    const reportSchema = roundOneReportSchema();
    const forwardedUnchanged = incidentToolSet().map((tool) => (tool === REPORT_TOOL ? geminiReportSchema(reportSchema) : tool.input_schema));
    const total = totalComplexity(forwardedUnchanged);
    expect(total.enumValues).toBeGreaterThan(KNOWN_GOOD_ROUND_ONE_BUDGET.enumValues);
    expect(total.properties).toBeGreaterThan(KNOWN_GOOD_ROUND_ONE_BUDGET.properties);
  });
});

/** Paths of nested object-typed properties that declare no properties; a top-level `{}` tool is fine. */
function emptyObjectProperties(schema: unknown, path: string): string[] {
  if (schema === null || typeof schema !== 'object' || Array.isArray(schema)) return [];
  const properties = (schema as { properties?: unknown }).properties;
  if (properties === null || typeof properties !== 'object' || Array.isArray(properties)) return [];
  return Object.entries(properties).flatMap(([key, child]) => {
    const childPath = `${path}.${key}`;
    const childProperties = (child as { properties?: unknown } | null)?.properties;
    const isObject = (child as { type?: unknown } | null)?.type === 'object';
    const empty = isObject && (childProperties === null || typeof childProperties !== 'object' || Object.keys(childProperties).length === 0);
    return empty ? [childPath] : emptyObjectProperties(child, childPath);
  });
}

/** Every `maxItems` in a schema tree; the bisect's trigger is a medium bound over an enum array. */
function maxItemsBounds(node: unknown, found: number[] = []): number[] {
  if (Array.isArray(node)) {
    for (const item of node) maxItemsBounds(item, found);
  } else if (node !== null && typeof node === 'object') {
    for (const [key, value] of Object.entries(node)) {
      if (key === 'maxItems' && typeof value === 'number') found.push(value);
      else maxItemsBounds(value, found);
    }
  }
  return found;
}

/** Paths of every array whose items are an enum. */
function enumArrays(node: unknown, path = '', found: string[] = []): string[] {
  if (Array.isArray(node)) {
    node.forEach((item, index) => enumArrays(item, `${path}.${index}`, found));
  } else if (node !== null && typeof node === 'object') {
    const schema = node as Record<string, unknown>;
    const isArray = schema.type === 'array' || (Array.isArray(schema.type) && schema.type.includes('array'));
    // Any enum anywhere inside an array's items counts, so a new shape (anyOf, oneOf) cannot slip past.
    if (isArray && schema.items && JSON.stringify(schema.items).includes('"enum"')) found.push(path.replace(/^\./, ''));
    for (const [key, value] of Object.entries(schema)) enumArrays(value, `${path}.${key}`, found);
  }
  return found;
}

describe('Gemini forced-call decoding states (2026-09-28 bisect, AGENTS.md §gemini-forced-call-states)', () => {
  it('offers Gemini no array bound above one item on any tool, the report included', () => {
    const tools = asProviderTools(providerFunctionTools(incidentToolSet(), roundOneReportSchema(), DEFAULT_MODEL));
    const bounds = tools.flatMap((tool) => maxItemsBounds(tool.function.parameters));
    expect(bounds.every((bound) => bound <= 1)).toBe(true);
    const goals = (tools.find((tool) => tool.function.name === 'search_environmental_strategies')
      ?.function.parameters.properties as Record<string, { description?: string }>).goals;
    expect(goals.description).toContain('Maximum item count: 6.');
  });

  it('keeps the catalogue bounds and enums for a non-Gemini model, so the projection is Gemini-only', () => {
    const [offered] = asProviderTools(providerFunctionTools([agriTool('search_environmental_strategies')], {}, 'openai/gpt-4.1-mini'));
    expect(maxItemsBounds(offered.function.parameters)).toContain(6);
    expect(enumArrays(offered.function.parameters)).toContain('properties.goals');
  });

  // AI Studio refused the whole round with "too many states" while Vertex accepted it: every enum array
  // is a repeating choice loop, and the loops summed past the limit once soil_properties_at_point was
  // published (two more). Flattening every evidence tool's enum arrays was the only variant AI Studio
  // accepted, at 15 and 60 report facts too; the report keeps its enums because its own validation reads them.
  it('offers Gemini no enum array on any evidence tool and names the allowed values instead', () => {
    const tools = asProviderTools(providerFunctionTools(incidentToolSet(), roundOneReportSchema(), DEFAULT_MODEL));
    const evidence = tools.filter((tool) => tool.function.name !== REPORT_TOOL.name);
    expect(evidence.flatMap((tool) => enumArrays(tool.function.parameters).map((path) => `${tool.function.name}.${path}`))).toEqual([]);
    const goals = (evidence.find((tool) => tool.function.name === 'search_environmental_strategies')
      ?.function.parameters.properties as Record<string, { items?: unknown; description?: string }>).goals;
    expect(goals.items).toEqual({ type: 'string' });
    expect(goals.description).toContain('Allowed values: soil_health, water_management,');
    expect(goals.description).toContain('Maximum item count: 6.');
  });
});

describe('provider-facing catalogue schemas', () => {
  it('omits only the server-owned and server-bound arguments from the request the loop actually sends', async () => {
    vi.stubEnv('OPENROUTER_MODEL', '');
    vi.spyOn(webEvidence, 'getWebEvidenceProvider').mockReturnValue(null);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const catalogueTools = agriTools();
    mocks.load.mockResolvedValue({ tools: catalogueTools, surfaces: ['vegetation'], featureSurfaces: [], valueSurfaces: ['vegetation'] });
    mocks.call.mockResolvedValue('{}');
    mocks.completionStream.mockImplementation(() => {
      throw new Error('stop after round one');
    });

    const run = async () => {
      for await (const event of streamRegionalIntelligence(payload, {}, true, temporal, [])) void event;
    };
    await expect(run()).rejects.toThrow('stop after round one');

    const sent = mocks.completionStream.mock.calls[0][0] as { model: string; tools: ProviderTool[] };
    expect(sent.model).toBe(DEFAULT_MODEL);
    const sentByName = new Map(sent.tools.map((tool) => [tool.function.name, tool.function.parameters]));
    for (const { function: tool } of AGRI_CATALOGUE_56467BD4) {
      const parameters = sentByName.get(tool.name);
      const withheld: readonly string[] = isStrategyKnowledgeTool(tool.name)
        ? SERVER_OWNED_LITERATURE_ARGUMENTS : SERVER_BOUND_EVIDENCE_ARGUMENTS[tool.name] ?? [];
      const catalogueProperties = tool.parameters.properties as Record<string, unknown>;
      const expectedProperties = Object.fromEntries(Object.entries(catalogueProperties).filter(([key]) => !withheld.includes(key)));
      // DEFAULT_MODEL is Gemini, so every tool also carries the bounds-as-instructions projection.
      expect(parameters, tool.name).toEqual(geminiEvidenceSchema({
        ...tool.parameters,
        properties: expectedProperties,
        ...(Array.isArray(tool.parameters.required) ? { required: tool.parameters.required.filter((key: unknown) => !withheld.includes(String(key))) } : {}),
      }));
    }
    expect(sentByName.get('search_environmental_strategies')).not.toHaveProperty('properties.site_profile');
    expect(sentByName.get('search_strategy_research_findings')).not.toHaveProperty('properties.region');
    expect(Object.keys(sentByName.get('surface_evidence_for_selection')?.properties as object)).toEqual(['surface_name', 'page_start']);
    // The shared catalogue keeps its full schema: the projection is a copy.
    expect(catalogueTools.find((tool) => tool.name === 'search_environmental_strategies')?.input_schema)
      .toHaveProperty('properties.site_profile');
  });
});

/** A placeholder value of the offered schema's type; the binding, not this value, is under test. */
function sampleArgument(key: string, schema: unknown): unknown {
  if (key === 'surface_name') return 'vegetation';
  const declared = (schema as { type?: unknown } | undefined)?.type;
  const type = Array.isArray(declared) ? declared.find((entry) => entry !== 'null') : declared;
  return type === 'number' || type === 'integer' ? 1 : type === 'object' ? {} : type === 'array' ? [] : type === 'boolean' ? false : 'x';
}

describe('provider schemas round-trip through strict server validation', () => {
  // AGENTS.md §provider-tool-budget (server-bound arguments): the provider view is only smaller, never
  // weaker, if every argument it withholds still reaches the reader from the map selection.
  it('supplies every catalogue argument the provider view withholds when the model sends only what it is offered', () => {
    const catalogue = [
      ...AGRI_CATALOGUE_CURRENT.map(({ function: tool }) => ({ name: tool.name, description: tool.description, input_schema: structuredClone(tool.parameters) })),
      ...landContextTools(),
    ];
    const offered = new Map(asProviderTools(providerFunctionTools(catalogue, {}, DEFAULT_MODEL))
      .map((tool) => [tool.function.name, tool.function.parameters]));
    let withheldCount = 0;
    for (const tool of catalogue) {
      // Literature withholds site_profile/region on purpose; the bridge rebuilds them from server_context (test above).
      if (isStrategyKnowledgeTool(tool.name)) continue;
      const parameters = offered.get(tool.name) ?? {};
      const offeredProperties = (parameters.properties ?? {}) as Record<string, unknown>;
      const catalogueProperties = (tool.input_schema.properties ?? {}) as Record<string, unknown>;
      const call = Object.fromEntries(((parameters.required ?? []) as string[]).map((key) => [key, sampleArgument(key, offeredProperties[key])]));
      const bound = bindProviderEvidenceArguments(tool.name, call, payload, temporal);
      const withheld = Object.keys(catalogueProperties).filter((key) => !(key in offeredProperties));
      withheldCount += withheld.length;
      for (const key of [...withheld, ...((tool.input_schema.required ?? []) as string[])]) {
        expect(bound, `${tool.name}.${key}`).toHaveProperty(key);
      }
      if ('bbox' in catalogueProperties) {
        expect(offeredProperties, tool.name).not.toHaveProperty('bbox');
        expect(bound.bbox, tool.name).toEqual({
          west: expect.any(Number), south: expect.any(Number), east: expect.any(Number), north: expect.any(Number),
        });
      }
    }
    expect(withheldCount).toBe(Object.values(SERVER_BOUND_EVIDENCE_ARGUMENTS).flat().length);
  });

  it('reads the selection tile for an area tool the model calls with no arguments at all', async () => {
    vi.stubEnv('OPENROUTER_MODEL', '');
    vi.spyOn(webEvidence, 'getWebEvidenceProvider').mockReturnValue(null);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    mocks.load.mockResolvedValue({ tools: landContextTools(), surfaces: [], featureSurfaces: [], valueSurfaces: [] });
    mocks.call.mockResolvedValue('{}');
    const areaTools = ['read_crop_cover_in_area', 'resolve_land_boundary_in_area', 'lookup_land_contacts_in_area'];
    mocks.completionStream.mockReturnValueOnce({
      [Symbol.asyncIterator]: () => (async function* () {})(),
      finalChatCompletion: async () => ({ choices: [{
        finish_reason: 'tool_calls',
        message: { role: 'assistant', content: null, refusal: null, tool_calls: areaTools.map((name) => ({
          id: name, type: 'function', function: { name, arguments: '{}' },
        })) },
      }] }),
    });
    mocks.completionStream.mockImplementation(() => {
      throw new Error('stop after round two');
    });

    const run = async () => {
      for await (const event of streamRegionalIntelligence(payload, {}, true, temporal, [])) void event;
    };
    await expect(run()).rejects.toThrow('stop after round two');

    const offered = (mocks.completionStream.mock.calls[0][0] as { tools: ProviderTool[] }).tools;
    for (const name of areaTools) {
      expect(offered.find((tool) => tool.function.name === name)?.function.parameters, name).not.toHaveProperty('properties.bbox');
      const [, args] = mocks.call.mock.calls.find(([called]) => called === name) ?? [];
      expect(args, name).toMatchObject({
        bbox: { west: expect.any(Number), south: expect.any(Number), east: expect.any(Number), north: expect.any(Number) },
      });
      expect(args.bbox.west, name).toBeLessThan(payload.location.lon);
      expect(args.bbox.east, name).toBeGreaterThan(payload.location.lon);
    }
    expect(mocks.call.mock.calls.find(([called]) => called === 'read_crop_cover_in_area')?.[1])
      .toMatchObject({ asOfDay: temporal.serverCurrentDate, zoomTier: 13 });
  });

  it('binds a literature call identically whether or not the model still sends server-owned arguments', () => {
    for (const name of ['search_environmental_strategies', 'search_strategy_research_findings']) {
      const [offered] = asProviderTools(providerFunctionTools([agriTool(name)], {}, DEFAULT_MODEL));
      const offeredProperties = offered.function.parameters.properties as Record<string, unknown>;
      const conforming = { query: 'reduce erosion after a wildfire', goals: ['erosion_control'], limit: 5 };
      expect(Object.keys(conforming).every((key) => key in offeredProperties)).toBe(true);
      expect(bindRegionalEvidenceArguments(name, conforming, payload, temporal)).toEqual(conforming);
      // A model ignoring the offered schema still reaches the agri pydantic validator with the same arguments.
      const stale = { ...conforming, site_profile: { soil_ph: 4, slope_pct: 50 }, region: ['pnw_inland'] };
      expect(bindRegionalEvidenceArguments(name, stale, payload, temporal)).toEqual(conforming);
      expect(catalogueParameters(name)).toHaveProperty('properties.site_profile');
    }
  });

  it('accepts a report valid under the Gemini projection and still rejects what the projection no longer bounds', () => {
    const tools = asProviderTools(providerFunctionTools([REPORT_TOOL], roundOneReportSchema(), DEFAULT_MODEL));
    const offered = tools[0].function.parameters;
    expect(offered).toHaveProperty('properties.observations.items.anyOf.0.properties.measurementFactId.enum', TWENTY_FACTS.map((fact) => fact.id));
    expect(offered).not.toHaveProperty('properties.riskSummary.properties.headline.maxLength');

    const serverValidate = (input: Record<string, unknown>) => {
      const resolved = resolveProviderMeasurementReport(groundLiteratureClaims(pairLiteratureProvenance(input), []), TWENTY_FACTS);
      return { issues: resolved.issues, parsed: remediationReportSchema.safeParse(normalizeProviderReport(resolved.report)) };
    };
    const valid = {
      riskSummary: { level: 'moderate', headline: 'Vegetation cover is sparse after the fire.', factors: [], evidenceOrigin: 'model_inference', evidenceSources: [] },
      observations: [
        { evidenceOrigin: 'warehouse', measurementFactId: TWENTY_FACTS[0].id },
        { statement: 'Sparse cover raises short-term erosion exposure.', evidenceOrigin: 'model_inference' },
      ],
      remediation: [],
      professionalConsultation: 'Consult a soil scientist before seeding.',
    };
    const accepted = serverValidate(valid);
    expect(accepted.issues).toEqual([]);
    expect(accepted.parsed.success).toBe(true);
    if (accepted.parsed.success) expect(accepted.parsed.data.observations[0]).toMatchObject({ statement: TWENTY_FACTS[0].statement, evidenceSource: 'vegetation' });

    const overLong = serverValidate({ ...valid, riskSummary: { ...valid.riskSummary, headline: 'x'.repeat(301) } });
    expect(overLong.parsed.success).toBe(false);
    const unknownFact = serverValidate({ ...valid, observations: [{ evidenceOrigin: 'warehouse', measurementFactId: 'fact-999-stale' }] });
    expect(unknownFact.issues).toEqual([expect.objectContaining({ path: ['observations', 0, 'measurementFactId'] })]);
  });
});
