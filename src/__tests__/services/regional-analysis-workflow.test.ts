import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { REGIONAL_TOOL_EVIDENCE_SOURCES, type RegionalAnalysisEvidence } from '@/lib/regional-intelligence';
import { readRegionalAnalysisEvidence } from '@/lib/regional-analysis-evidence';
import type { RegionalContextPayload, TemporalContext, ViewedLayerReading } from '@/lib/server/services/regional-context';

const mocks = vi.hoisted(() => ({ load: vi.fn(), call: vi.fn() }));
vi.mock('@/lib/server/services/regional-evidence-tools', () => ({
  loadRegionalEvidenceTools: mocks.load, callRegionalEvidenceTool: mocks.call,
}));
import { bindRegionalEvidenceArguments, boundedEvidence, evidenceResultStatus, prepareRegionalAnalysis, REGIONAL_ANALYSIS_PRIORITY_SURFACES, regionalEvidenceAuditCall, regionalEvidenceDay, regionalEvidenceStageStatus, STRATEGY_SCREENING } from '@/lib/server/services/regional-analysis-workflow';
import { analysisDateRange } from '@/lib/regional-analysis-selection';
import { LAYER_REGISTRY } from '@/lib/map/layer-registry';
import { REMEDIATION_REPORT_JSON_SCHEMA, normalizeProviderReport, remediationReportSchema, reportCitationManifest, reportSchemaForCitations, reportWarehouseEvidenceIssues } from '@/lib/server/services/remediation-report';

const payload: RegionalContextPayload = {
  location: { lat: 44, lon: -118, geohash: '9r' },
  waterScarcity: null, soilProperties: null, weather: null, fireDetections: null,
  firePerimeters: null, mtbsPerimeters: null, carbonPotential: null,
  strategyRecommendations: null, strategyContext: [], communityProposals: [],
};
const reading = (layer: string, viewedDate: string): ViewedLayerReading => ({
  layer, viewedDate, clientReportsDataOnDate: true, evidenceSource: null,
  outcome: 'not_represented_in_payload', reason: null, clientClaimContradicted: false,
  setCorrespondence: 'no_payload_block_for_this_row',
});
const temporal: TemporalContext = {
  serverCurrentDate: '2026-09-12', viewedLayersUnreported: false,
  readings: [
    reading('climate-precipitation', '2024-06-15'),
    reading('climate-soil-wetness-root-zone', '2023-06-15'),
    reading('soil-moisture', '2022-06-15'),
    reading('soil-temperature', '2022-06-16'),
    reading('soil-vpd', '2022-06-17'),
  ],
  viewedDates: ['2022-06-15', '2022-06-16', '2022-06-17', '2023-06-15', '2024-06-15'], sourcesServedAsOfLatest: [],
};
const toolNames = ['surface_evidence_for_selection', 'list_environmental_layers'];

beforeEach(() => {
  mocks.load.mockResolvedValue({ tools: toolNames.map((name) => ({ name, description: name, input_schema: {} })), surfaces: [...REGIONAL_TOOL_EVIDENCE_SOURCES], featureSurfaces: [], valueSurfaces: [] });
  mocks.call.mockResolvedValue(JSON.stringify({ features: [{ served_day: '2024-06-15', properties: { value: 0.4, unit: 'm3/m3' } }], day_state: { state: 'published' } }));
});
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });

describe('regional evidence graph', () => {
  it('prefetches relevant exact tiles and active history while keeping the full layer catalogue available', async () => {
    const result = await prepareRegionalAnalysis(payload, temporal);
    expect(result.evidence.toolCalls.filter((call) => call.stage === 'local')).toHaveLength(6);
    expect(JSON.parse(result.context).availableLayers).toEqual([...REGIONAL_TOOL_EVIDENCE_SOURCES]);
    expect(JSON.parse(result.context).observations).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'local-1', evidenceReadId: 'local-1', evidenceSource: 'climate-field-precipitation', evidenceStatus: 'observed' }),
    ]));
    expect(result.evidence.stages.map((stage) => stage.id)).toEqual(['inventory', 'local', 'temporal', 'strategies']);
    const local = result.evidence.toolCalls.filter((call) => call.stage === 'local');
    expect(local.find((call) => call.source === 'climate-field-precipitation')?.selectedDate).toBe('2024-06-15');
    expect(local.find((call) => call.source === 'climate-field-soil-wetness-root-zone')?.selectedDate).toBe('2023-06-15');
    expect(local.find((call) => call.source === 'soil-field-moisture')?.selectedDate).toBe('2022-06-15');
    expect(local.find((call) => call.source === 'soil-field-temperature')?.selectedDate).toBe('2022-06-16');
    expect(local.find((call) => call.source === 'soil-field-vpd')?.selectedDate).toBe('2022-06-17');
    expect(REGIONAL_ANALYSIS_PRIORITY_SURFACES).toEqual(expect.arrayContaining([
      'soil-field-moisture', 'soil-field-temperature', 'soil-field-vpd',
    ]));
    const past = result.evidence.toolCalls.filter((call) => call.stage === 'temporal');
    expect(past).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'climate-field-precipitation', selectedDate: '2024-06-15', rangeStart: '2024-05-15', rangeEnd: '2024-07-15', timeScale: 'month' }),
      expect.objectContaining({ source: 'climate-field-soil-wetness-root-zone', selectedDate: '2023-06-15', rangeStart: '2023-05-15', rangeEnd: '2023-07-15' }),
    ]));
    expect(mocks.call.mock.calls.every(([name, args]) => name === 'surface_evidence_for_selection'
      && args.longitude === payload.location.lon && args.latitude === payload.location.lat
      && !('radius_meters' in args))).toBe(true);
    expect(result.context).toContain('feedstock and production conditions');
    expect(STRATEGY_SCREENING.map((strategy) => strategy.strategy)).toEqual(expect.arrayContaining(['silvopasture', 'biochar', 'managed_grazing', 'fuel_reduction', 'keyline']));
    expect(readRegionalAnalysisEvidence(result.evidence)).toEqual(result.evidence);
  });

  it('resolves both canonical surface names and client toggle IDs', () => {
    expect(regionalEvidenceDay(temporal, 'soil-field-moisture')).toBe('2022-06-15');
    expect(regionalEvidenceDay({ ...temporal, readings: [reading('soil-field-vpd', '2021-01-01')] }, 'soil-field-vpd')).toBe('2021-01-01');
  });

  it('keeps an unobservable calendar day from crashing historical planning or corrupting the audit', async () => {
    mocks.call.mockRejectedValue(new Error('invalid calendar day'));
    const result = await prepareRegionalAnalysis(payload, {
      ...temporal, readings: [reading('climate-precipitation', '2025-02-30')], viewedDates: ['2025-02-30'],
    });
    expect(readRegionalAnalysisEvidence(result.evidence)).not.toBeNull();
    expect(result.evidence.toolCalls.every((call) => call.status === 'error')).toBe(true);
    expect(result.evidence.toolCalls.every((call) => call.selectedDate === undefined)).toBe(true);
    expect(mocks.call.mock.calls.some((call) => call[1].day === '2025-02-30')).toBe(true);
  });

  it('reserves selected-window history when local reads exhaust their deadline', async () => {
    vi.useFakeTimers();
    let active = 0;
    let maximum = 0;
    mocks.call.mockImplementation((_tool: string, _args: unknown, signal: AbortSignal) => new Promise((_resolve, reject) => {
      active += 1;
      maximum = Math.max(maximum, active);
      signal.addEventListener('abort', () => { active -= 1; reject(new Error('aborted')); }, { once: true });
    }));
    const pending = prepareRegionalAnalysis(payload, temporal);
    await vi.advanceTimersByTimeAsync(33_000);
    const result = await pending;
    expect(maximum).toBe(3);
    expect(mocks.call.mock.calls.slice(0, 3).map((call) => call[1].surface_name)).toEqual([
      'climate-field-precipitation', 'climate-field-soil-wetness-root-zone', 'soil-field-moisture',
    ]);
    expect(result.evidence.toolCalls.some((call) => call.stage === 'local' && call.status === 'not_queried')).toBe(true);
    expect(result.evidence.toolCalls.some((call) => call.stage === 'temporal' && call.status === 'error')).toBe(true);
    expect(result.evidence.toolCalls.some((call) => call.status === 'observed')).toBe(false);
  });

  it('keeps failed catalogue discovery explicit without attempting any data tools', async () => {
    mocks.load.mockRejectedValue(new Error('unavailable'));
    const result = await prepareRegionalAnalysis(payload, temporal);
    expect(mocks.call).not.toHaveBeenCalled();
    expect(result.evidence.stages.every((stage) => stage.status === 'unavailable')).toBe(true);
    expect(result.evidence.limitations.join(' ')).toContain('historical and regional comparisons were not performed');
  });

  it('retains independently selected hidden dates and binds all continuation reads to the latest selection', () => {
    const current = { ...temporal, analysisSelection: {
      timeScale: 'year' as const, rangeSteps: 2, zoom: 8.75,
      layerDays: { 'soil-vpd': '2024-02-29', vegetation: '2020-03-31' },
    } };
    const args = bindRegionalEvidenceArguments('surface_evidence_for_selection', {
      surface_name: 'soil-vpd', day: '2026-09-01', longitude: 1, latitude: 2,
      range_start: '2026-01-01', range_end: '2026-12-31', zoom: 3, page_start: 31,
    }, payload, current);
    expect(args).toEqual({ surface_name: 'soil-field-vpd', day: '2024-02-29', longitude: -118,
      latitude: 44, range_start: '2022-02-28', range_end: '2026-02-28', zoom: 8.75,
      time_scale: 'year', page_start: 31 });
    expect(regionalEvidenceDay(current, 'vegetation')).toBe('2020-03-31');
  });

  it('uses inclusive calendar windows across leap years and unequal months', () => {
    expect(analysisDateRange('2024-03-31', 'month', 1)).toEqual({ rangeStart: '2024-02-29', rangeEnd: '2024-04-30' });
    expect(analysisDateRange('2024-02-29', 'year', 1)).toEqual({ rangeStart: '2023-02-28', rangeEnd: '2025-02-28' });
    expect(analysisDateRange('2024-12-31', 'day', 2)).toEqual({ rangeStart: '2024-12-29', rangeEnd: '2025-01-02' });
  });

  it('binds land-context coordinate aliases and area queries to the active selection tile', () => {
    expect(bindRegionalEvidenceArguments('resolve_land_boundary_at_point', { lon: 5, lat: 6 }, payload, temporal))
      .toEqual({ lon: -118, lat: 44 });
    const args = bindRegionalEvidenceArguments('resolve_land_boundary_in_area', {
      bbox: { west: 1, south: 1, east: 2, north: 2 },
    }, payload, { ...temporal, analysisSelection: { timeScale: 'day', rangeSteps: 1, zoom: 10, layerDays: {} } });
    const bbox = args.bbox as { west: number; south: number; east: number; north: number };
    expect(bbox.west).toBeLessThanOrEqual(-118);
    expect(bbox.east).toBeGreaterThan(-118);
    expect(bbox.south).toBeLessThanOrEqual(44);
    expect(bbox.north).toBeGreaterThan(44);
    expect(bbox.east - bbox.west).toBeCloseTo(360 / 2 ** 10);
  });

  it('admits every switchable map surface as an evidence citation regardless of visibility', () => {
    for (const layer of Object.values(LAYER_REGISTRY)) {
      expect(REGIONAL_TOOL_EVIDENCE_SOURCES).toContain(layer.warehouseLayerName ?? layer.toggleId);
    }
  });
});

describe('evidence audit honesty', () => {
  it('keeps two years of weekly drought history instead of reducing it to eight recent releases', () => {
    const weekly = Array.from({ length: 104 }, (_, week) => ({ week, severity_class: week < 52 ? 3 : 0 }));
    expect(boundedEvidence({ weekly_severity: weekly })).toEqual({ weekly_severity: weekly });
  });
  it('keeps requested dates separate from every bounded actual date provenance field', () => {
    const entry = regionalEvidenceAuditCall('local-1', 'local', 'surface_value_near_point', { surface_name: 'watersheds', day: '2026-09-10' }, {
      lanes: [{ served_day: '2026-09-01', features: [{ served_day: '2026-09-01', observed_day: '2026-08-31' }] }, { served_day: '2026-02-30' }],
      history: [{ valid_date: '2026-08-30' }, { valid_date: 'invalid' }],
      observed_at: '2026-08-30T23:00:00-07:00',
    });
    expect(entry.selectedDate).toBe('2026-09-10');
    expect(entry.validDates).toEqual(['2026-08-30']);
    expect(entry.observedDates).toEqual(['2026-08-31']);
    expect(entry.servedDates).toEqual(['2026-09-01']);
    expect(readRegionalAnalysisEvidence({ version: 1, stages: [], toolCalls: [entry], limitations: [] })).not.toBeNull();
  });
  it('attributes combined fire history only to physical lanes with actual returned rows', () => {
    const entry = regionalEvidenceAuditCall('temporal-fire', 'temporal', 'fire_history_near_point', {
      longitude: -116, latitude: 44, as_of_day: '2026-09-10',
    }, { layer_summaries: [{ layer_name: 'burn-severity', row_count: 1 }, { layer_name: 'fire-detections', row_count: 2 }] });
    expect(entry).not.toHaveProperty('source');
    expect(entry.sources).toEqual(['burn-severity', 'fire-detections']);
    const partial = regionalEvidenceAuditCall('temporal-fire', 'temporal', 'fire_history_near_point', {
      longitude: -116, latitude: 44, as_of_day: '2026-09-10',
    }, { layer_summaries: [
      { layer_name: 'burn-severity', row_count: 0 },
      { layer_name: 'fire-detections', row_count: 2, earliest_observed_day: '2024-09-10', latest_observed_day: '2026-09-09' },
    ] });
    expect(partial.sources).toEqual(['fire-detections']);
    expect(partial.observedDates).toEqual(['2024-09-10', '2026-09-09']);
    expect(readRegionalAnalysisEvidence({ version: 1, stages: [], toolCalls: [partial], limitations: [] })).not.toBeNull();
    const report = { riskSummary: { level: 'low' as const, headline: 'Burn history.', factors: [], evidenceOrigin: 'warehouse' as const, evidenceSources: ['burn-severity' as const], evidenceReadIds: ['temporal-fire'] }, observations: [], remediation: [], professionalConsultation: 'Consult a forester.' };
    expect(reportWarehouseEvidenceIssues(report, payload, { version: 1, stages: [], toolCalls: [partial], limitations: [] }).length).toBeGreaterThan(0);
  });
  it('does not present availability bounds as observed measurement dates', () => {
    const entry = regionalEvidenceAuditCall('coverage-1', 'inventory', 'observation_coverage_on_day', {
      surface_name: 'weather-observations', day: '2026-09-10',
    }, {
      coverage: { earliest_observed_day: '2020-01-01', latest_observed_day: '2026-09-09' },
      cells: [{ state: 'published' }],
    });
    expect(entry).not.toHaveProperty('observedDates');
  });
  it('bounds total actual date provenance across all three warehouse date fields', () => {
    const history = Array.from({ length: 200 }, (_, index) => ({
      valid_date: new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10),
      observed_day: new Date(Date.UTC(2024, 0, index + 1)).toISOString().slice(0, 10),
      served_day: new Date(Date.UTC(2023, 0, index + 1)).toISOString().slice(0, 10),
    }));
    const entry = regionalEvidenceAuditCall('temporal-1', 'temporal', 'drought_history_at_point', {}, { history });
    expect((entry.validDates?.length ?? 0) + (entry.observedDates?.length ?? 0) + (entry.servedDates?.length ?? 0)).toBeLessThanOrEqual(128);
  });
  it.each(['features', 'rows', 'weekly_severity'])('recognizes real %s payload rows', (key) => {
    expect(evidenceResultStatus({ [key]: [{ observed_day: '2023-06-15', severity_class: null }] }).status).toBe('observed');
  });
  it('distinguishes empty history summaries, refusals and governed absence from observations', () => {
    expect(evidenceResultStatus({ layer_summaries: [{ feature_count: 0, row_count: 0 }] }).status).toBe('unavailable');
    expect(evidenceResultStatus({ layer_summaries: [{ feature_count: 2, row_count: 2 }] }).status).toBe('observed');
    expect(evidenceResultStatus({ error: 'parquet_availability_withheld' }).status).toBe('refused');
    expect(evidenceResultStatus({ features: [], day_state: { state: 'governed_absence' } }).status).toBe('governed_absence');
    expect(evidenceResultStatus({ lanes: [{ selected: { state: 'published', features: [{ properties: { value: 0.5 } }] }, history: [{ state: 'lane_never_written', features: [] }] }] }).status).toBe('observed');
    expect(evidenceResultStatus({ lanes: [{ history: [{ state: 'lane_never_written', features: [] }] }], history: { sampled_days: ['2024-01-01'] } }).status).toBe('unavailable');
  });
  it.each(['vegetation', '', '   '])('keeps malformed model arguments from invalidating the audit for %j', (source) => {
    const entry = regionalEvidenceAuditCall('additional-1', 'additional', 'surface_value_near_point', { surface_name: source, day: 'yesterday', latitude: 999, longitude: -118 }, { error: 'invalid_coordinate' });
    expect(entry.status).toBe('refused');
    expect(entry).not.toHaveProperty('selectedDate');
    expect(entry).not.toHaveProperty('location');
    expect(readRegionalAnalysisEvidence({ version: 1, stages: [], toolCalls: [entry], limitations: [] })).not.toBeNull();
  });
  it('derives completed, partial and unavailable stage states from all attempts', () => {
    const call = (status: RegionalAnalysisEvidence['toolCalls'][number]['status']) => ({
      id: status, stage: 'additional', tool: 'reader', status,
    });
    expect(regionalEvidenceStageStatus([call('observed'), call('governed_absence')])).toBe('completed');
    expect(regionalEvidenceStageStatus([call('observed'), call('error')])).toBe('partial');
    expect(regionalEvidenceStageStatus([call('refused'), call('not_queried'), call('unavailable')])).toBe('unavailable');
  });
  it('requires exact observed read references for comparisons and follow-up findings', () => {
    const report = {
      riskSummary: { level: 'low' as const, headline: 'Interventions are available here. No data were omitted.', factors: [], evidenceOrigin: 'warehouse' as const, evidenceSources: ['interventions' as const] },
      observations: [], remediation: [], professionalConsultation: 'Consult an agronomist.',
    };
    const evidence: RegionalAnalysisEvidence = { version: 1, stages: [], limitations: [], toolCalls: [
      { id: 'one', stage: 'local', tool: 'surface_value_near_point', source: 'interventions', status: 'governed_absence' },
    ] };
    expect(reportWarehouseEvidenceIssues(report, payload, evidence)).toHaveLength(1);
    expect(reportWarehouseEvidenceIssues(report, payload, { ...evidence, toolCalls: [{ ...evidence.toolCalls[0], status: 'observed' }] })).toHaveLength(1);
    expect(reportWarehouseEvidenceIssues({ ...report, riskSummary: { ...report.riskSummary, evidenceReadIds: ['one'] } }, payload,
      { ...evidence, toolCalls: [{ ...evidence.toolCalls[0], status: 'observed' }] })).toEqual([]);
    for (const stage of ['temporal', 'regional', 'additional'] as const) {
      const comparisonEvidence: RegionalAnalysisEvidence = { ...evidence, toolCalls: [{ ...evidence.toolCalls[0], stage, status: 'observed', selectedDate: '2024-05-01', location: { lat: 44, lon: -116.5 } }] };
      expect(reportWarehouseEvidenceIssues(report, payload, comparisonEvidence)).toHaveLength(1);
      const cited = { ...report, riskSummary: { ...report.riskSummary, evidenceReadIds: ['one'] } };
      expect(reportWarehouseEvidenceIssues(cited, payload, comparisonEvidence)).toEqual([]);
      expect(reportWarehouseEvidenceIssues(cited, payload, { ...comparisonEvidence, toolCalls: [{ ...comparisonEvidence.toolCalls[0], source: 'vegetation' }] }).length).toBeGreaterThan(0);
    }
    const invalidReference = { ...report, riskSummary: { ...report.riskSummary, evidenceReadIds: ['invented'] } };
    expect(reportWarehouseEvidenceIssues(invalidReference, payload, evidence).length).toBeGreaterThan(0);
    const refusedReference = { ...report, riskSummary: { ...report.riskSummary, evidenceReadIds: ['one'] } };
    expect(reportWarehouseEvidenceIssues(refusedReference, payload, evidence).length).toBeGreaterThan(0);
    for (const tool of ['observation_coverage_on_day', 'observation_temporal_neighbors', 'list_environmental_layers']) {
      const metadata: RegionalAnalysisEvidence = { ...evidence, toolCalls: [{ ...evidence.toolCalls[0], tool, status: 'observed' }] };
      expect(reportWarehouseEvidenceIssues(refusedReference, payload, metadata).length).toBeGreaterThan(0);
      expect(reportWarehouseEvidenceIssues(report, payload, metadata)).toHaveLength(1);
    }
  });
  it('scopes provider citations to actual measurement pairs and preserves gap-only inference reports', () => {
    const canonical = JSON.stringify(REMEDIATION_REPORT_JSON_SCHEMA);
    const emptyManifest = reportCitationManifest(payload, undefined);
    expect(emptyManifest).toEqual({ payloadSources: [], measurementReads: [] });
    const emptySchema = reportSchemaForCitations(emptyManifest);
    expect(emptySchema).toHaveProperty('properties.riskSummary.properties.evidenceSources.maxItems', 0);
    expect(emptySchema).toHaveProperty('properties.riskSummary.properties.evidenceOrigin.enum', ['web', 'model_inference']);
    expect(emptySchema).not.toHaveProperty('properties.observations.items.properties.evidenceSource');
    expect(emptySchema).not.toHaveProperty('properties.observations.items.properties.evidenceReadIds');

    const evidence: RegionalAnalysisEvidence = { version: 1, stages: [], limitations: [], toolCalls: [
      { id: 'temporal-vpd', stage: 'temporal', tool: 'surface_evidence_for_selection', source: 'soil-field-vpd', status: 'observed', selectedDate: '2026-09-15' },
      { id: 'failed-vegetation', stage: 'local', tool: 'surface_evidence_for_selection', source: 'vegetation', status: 'refused' },
      { id: 'coverage', stage: 'local', tool: 'observation_coverage_on_day', source: 'climate-field-precipitation', status: 'observed' },
      { id: 'internal-lane', stage: 'local', tool: 'surface_evidence_for_selection', source: 'metric_vpd', status: 'observed' },
    ] };
    const manifest = reportCitationManifest(payload, evidence);
    expect(manifest.payloadSources).toEqual([]);
    expect(manifest.measurementReads).toEqual([expect.objectContaining({ evidenceSource: 'soil-field-vpd', evidenceReadId: 'temporal-vpd', selectedDate: '2026-09-15' })]);
    const schema = reportSchemaForCitations(manifest);
    expect(schema).toHaveProperty('properties.observations.items.anyOf.0.properties.evidenceSource.enum', ['soil-field-vpd']);
    expect(schema).toHaveProperty('properties.observations.items.anyOf.0.properties.evidenceReadIds.items.enum', ['temporal-vpd']);
    const claimFields = [
      { path: 'properties.riskSummary', required: ['level', 'headline', 'factors', 'evidenceOrigin', 'evidenceSources'] },
      { path: 'properties.observations.items', required: ['statement', 'evidenceOrigin'] },
      { path: 'properties.remediation.items', required: ['strategy', 'title', 'rationale', 'timeframe', 'confidence', 'consultProfessionals', 'evidenceOrigin'] },
    ];
    for (const { path, required } of claimFields) {
      expect(schema).not.toHaveProperty(`${path}.properties`);
      expect(schema).not.toHaveProperty(`${path}.required`);
      expect(schema).toHaveProperty(`${path}.anyOf`, expect.any(Array));
      for (const index of [0, 1]) {
        const branch = `${path}.anyOf.${index}`;
        expect(schema).toHaveProperty(`${branch}.type`, 'object');
        expect(schema).toHaveProperty(`${branch}.additionalProperties`, false);
        const warehouseRequired = [...required, ...(path === 'properties.riskSummary' ? [] : ['evidenceSource']), 'evidenceReadIds'];
        expect(schema).toHaveProperty(`${branch}.required`, index === 0 ? warehouseRequired : required);
        for (const field of required) expect(schema).toHaveProperty(`${branch}.properties.${field}`);
      }
      expect(schema).toHaveProperty(`${path}.anyOf.0.properties.evidenceOrigin.enum`, ['warehouse']);
      expect(schema).toHaveProperty(`${path}.anyOf.0.properties.evidenceReadIds.minItems`, 1);
      expect(schema).toHaveProperty(`${path}.anyOf.0.properties.evidenceReadIds.maxItems`, 8);
      expect(schema).toHaveProperty(`${path}.anyOf.1.properties.evidenceOrigin.enum`, ['web', 'model_inference']);
      expect(schema).not.toHaveProperty(`${path}.anyOf.1.properties.evidenceReadIds`);
    }
    const report = {
      riskSummary: { level: 'moderate' as const, headline: 'VPD observations are available.', factors: [], evidenceOrigin: 'warehouse' as const, evidenceSources: ['soil-field-vpd' as const], evidenceReadIds: ['temporal-vpd'] },
      observations: [], remediation: [], professionalConsultation: 'Consult an agronomist.',
    };
    expect(reportWarehouseEvidenceIssues(report, payload, evidence)).toEqual([]);
    const legacyPayload = { ...payload, waterScarcity: { droughtClass: 'D1', nearestGauge: null } };
    expect(reportCitationManifest(legacyPayload, evidence).payloadSources).toEqual(['drought']);
    const legacySchema = reportSchemaForCitations(reportCitationManifest(legacyPayload, evidence));
    for (const { path, required } of claimFields) {
      expect(legacySchema).toHaveProperty(`${path}.anyOf.2.type`, 'object');
      expect(legacySchema).toHaveProperty(`${path}.anyOf.2.additionalProperties`, false);
      expect(legacySchema).toHaveProperty(`${path}.anyOf.2.required`, required);
      for (const field of required) expect(legacySchema).toHaveProperty(`${path}.anyOf.2.properties.${field}`);
      expect(legacySchema).not.toHaveProperty(`${path}.anyOf.2.properties.evidenceReadIds`);
    }
    expect(legacySchema).toHaveProperty('properties.observations.items.anyOf.2.properties.evidenceOrigin.enum', ['warehouse']);
    expect(legacySchema).toHaveProperty('properties.observations.items.anyOf.2.properties.evidenceSource.enum', ['drought']);
    expect(legacySchema).toHaveProperty('properties.riskSummary.anyOf.2.properties.evidenceSources.items.enum', ['drought']);
    expect(reportWarehouseEvidenceIssues({ ...report, riskSummary: { ...report.riskSummary, evidenceSources: ['drought', 'soil-field-vpd'] } }, legacyPayload, evidence)).toEqual([]);
    expect(JSON.stringify(REMEDIATION_REPORT_JSON_SCHEMA)).toBe(canonical);
  });
  it('binds each singular warehouse source to its own read IDs and refreshes only that source after new reads', () => {
    const evidence: RegionalAnalysisEvidence = { version: 1, stages: [], limitations: [], toolCalls: [
      { id: 'local-vpd', stage: 'local', tool: 'surface_evidence_for_selection', source: 'soil-field-vpd', status: 'observed' },
      { id: 'temporal-vpd', stage: 'temporal', tool: 'surface_evidence_for_selection', source: 'soil-field-vpd', status: 'observed' },
      { id: 'local-vegetation', stage: 'local', tool: 'surface_evidence_for_selection', source: 'vegetation', status: 'observed' },
      { id: 'refused-precipitation', stage: 'local', tool: 'surface_evidence_for_selection', source: 'climate-field-precipitation', status: 'refused' },
    ] };
    const schema = reportSchemaForCitations(reportCitationManifest(payload, evidence));
    const snapshot = JSON.stringify(schema);
    const additionalEvidence: RegionalAnalysisEvidence = { ...evidence, toolCalls: [...evidence.toolCalls,
      { id: 'additional-vegetation', stage: 'additional', tool: 'surface_evidence_for_selection', source: 'vegetation', status: 'observed' },
    ] };
    const refreshed = reportSchemaForCitations(reportCitationManifest(payload, additionalEvidence));
    for (const path of ['properties.observations.items', 'properties.remediation.items']) {
      expect(schema).toHaveProperty(`${path}.anyOf`, expect.any(Array));
      expect(schema).not.toHaveProperty(`${path}.anyOf.3`);
      expect(schema).toHaveProperty(`${path}.anyOf.0.properties.evidenceSource.enum`, ['soil-field-vpd']);
      expect(schema).toHaveProperty(`${path}.anyOf.0.properties.evidenceReadIds.items.enum`, ['local-vpd', 'temporal-vpd']);
      expect(schema).toHaveProperty(`${path}.anyOf.1.properties.evidenceSource.enum`, ['vegetation']);
      expect(schema).toHaveProperty(`${path}.anyOf.1.properties.evidenceReadIds.items.enum`, ['local-vegetation']);
      for (const index of [0, 1]) {
        expect(schema).toHaveProperty(`${path}.anyOf.${index}.required`, expect.arrayContaining(['evidenceSource', 'evidenceReadIds']));
      }
      expect(schema).not.toHaveProperty(`${path}.anyOf.2.properties.evidenceReadIds`);
      expect(refreshed).toHaveProperty(`${path}.anyOf.0.properties.evidenceReadIds.items.enum`, ['local-vpd', 'temporal-vpd']);
      expect(refreshed).toHaveProperty(`${path}.anyOf.1.properties.evidenceReadIds.items.enum`, ['local-vegetation', 'additional-vegetation']);
    }
    expect(JSON.stringify(schema)).toBe(snapshot);
    const mismatched = remediationReportSchema.parse({
      riskSummary: { level: 'moderate', headline: 'Evidence is limited.', factors: [], evidenceOrigin: 'model_inference', evidenceSources: [] },
      observations: [{ statement: 'VPD is measured.', evidenceOrigin: 'warehouse', evidenceSource: 'soil-field-vpd', evidenceReadIds: ['local-vegetation'] }],
      remediation: [], professionalConsultation: 'Consult an agronomist.',
    });
    expect(reportWarehouseEvidenceIssues(mismatched, payload, evidence).length).toBeGreaterThan(0);
    expect(normalizeProviderReport(mismatched)).toHaveProperty('observations.0.evidenceReadIds', ['local-vegetation']);
    const matching = { ...mismatched, observations: [{ ...mismatched.observations[0], evidenceReadIds: ['local-vpd', 'temporal-vpd'] }] };
    expect(reportWarehouseEvidenceIssues(matching, payload, evidence)).toEqual([]);
  });
  it('normalizes only empty provider arrays on explicit nonwarehouse claims', () => {
    const report = {
      riskSummary: { level: 'moderate', headline: 'Evidence is limited.', factors: [], evidenceOrigin: 'model_inference', evidenceSources: [], evidenceReadIds: [] },
      observations: [{ statement: 'General guidance.', evidenceOrigin: 'web', evidenceReadIds: [] }],
      remediation: [], professionalConsultation: 'Consult an agronomist.',
    };
    const normalized = normalizeProviderReport(report);
    expect(normalized).not.toHaveProperty('riskSummary.evidenceReadIds');
    expect(normalized).not.toHaveProperty('observations.0.evidenceReadIds');
    expect(report).toHaveProperty('riskSummary.evidenceReadIds', []);
    expect(remediationReportSchema.safeParse(normalized).success).toBe(true);
    for (const ids of [['one'], null, 'one', {}, ['']]) {
      const invalid = normalizeProviderReport({ ...report, observations: [{ ...report.observations[0], evidenceReadIds: ids }] });
      expect(invalid).toHaveProperty('observations.0.evidenceReadIds', ids);
      expect(remediationReportSchema.safeParse(invalid).success).toBe(false);
    }
    for (const evidenceOrigin of ['warehouse', 'unknown']) {
      const invalid = normalizeProviderReport({ ...report, observations: [{ ...report.observations[0], evidenceOrigin, evidenceSource: 'vegetation' }] });
      expect(invalid).toHaveProperty('observations.0.evidenceReadIds', []);
      expect(remediationReportSchema.safeParse(invalid).success).toBe(false);
    }
    const nested = normalizeProviderReport({ ...report, observations: [{ ...report.observations[0], custom: { evidenceOrigin: 'web', evidenceReadIds: [] } }] });
    expect(nested).toHaveProperty('observations.0.custom.evidenceReadIds', []);
    expect(remediationReportSchema.safeParse(nested).success).toBe(false);
  });
  it('grounds each legacy citation only in its distinct assembled payload block', () => {
    const cited = (source: 'drought' | 'streamflow') => ({
      riskSummary: { level: 'low' as const, headline: 'Current assembled context.', factors: [], evidenceOrigin: 'warehouse' as const, evidenceSources: [source] },
      observations: [], remediation: [], professionalConsultation: 'Consult an agronomist.',
    });
    const droughtOnly = { ...payload, waterScarcity: { droughtClass: 'D1', nearestGauge: null } };
    expect(reportWarehouseEvidenceIssues(cited('drought'), droughtOnly, undefined)).toEqual([]);
    expect(reportWarehouseEvidenceIssues(cited('streamflow'), droughtOnly, undefined)).toHaveLength(1);
    const noDrought = { ...payload, waterScarcity: { droughtClass: null, nearestGauge: null } };
    expect(reportWarehouseEvidenceIssues(cited('drought'), noDrought, undefined, { drought: '2026-09-08T00:00:00Z' })).toEqual([]);
    expect(reportWarehouseEvidenceIssues(cited('drought'), noDrought, undefined, { drought: 'unavailable' })).toHaveLength(1);
    expect(reportWarehouseEvidenceIssues(cited('drought'), noDrought, undefined, { streamflow: '2026-09-08T00:00:00Z' })).toHaveLength(1);
    expect(reportWarehouseEvidenceIssues(cited('drought'), payload, {
      version: 1, stages: [], limitations: [], toolCalls: [
        { id: 'one', stage: 'local', tool: 'surface_value_near_point', source: 'drought-areas', status: 'observed' },
      ],
    })).toHaveLength(1);
    const legacyWithRead = { ...cited('drought'), riskSummary: { ...cited('drought').riskSummary, evidenceReadIds: ['one'] } };
    expect(reportWarehouseEvidenceIssues(legacyWithRead, droughtOnly, {
      version: 1, stages: [], limitations: [], toolCalls: [
        { id: 'one', stage: 'local', tool: 'surface_value_near_point', source: 'drought-areas', status: 'observed' },
      ],
    }).length).toBeGreaterThan(0);
  });
  it('requires every risk-summary ID and every declared tool source to correspond', () => {
    const report = {
      riskSummary: { level: 'low' as const, headline: 'Two tool sources.', factors: [], evidenceOrigin: 'warehouse' as const, evidenceSources: ['interventions' as const, 'vegetation' as const], evidenceReadIds: ['combined'] },
      observations: [], remediation: [], professionalConsultation: 'Consult an agronomist.',
    };
    const combined: RegionalAnalysisEvidence = { version: 1, stages: [], limitations: [], toolCalls: [
      { id: 'combined', stage: 'additional', tool: 'fire_history_near_point', sources: ['interventions', 'vegetation'], status: 'observed' },
    ] };
    expect(reportWarehouseEvidenceIssues(report, payload, combined)).toEqual([]);
    expect(reportWarehouseEvidenceIssues(report, payload, { ...combined, toolCalls: [{ ...combined.toolCalls[0], sources: ['interventions', 'fire-detections'] }] }).length).toBeGreaterThan(0);
  });
});
