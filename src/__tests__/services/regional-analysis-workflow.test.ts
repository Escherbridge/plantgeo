import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { REGIONAL_TOOL_EVIDENCE_SOURCES, type RegionalAnalysisEvidence } from '@/lib/regional-intelligence';
import { readRegionalAnalysisEvidence } from '@/lib/regional-analysis-evidence';
import type { RegionalContextPayload, TemporalContext, ViewedLayerReading } from '@/lib/server/services/regional-context';

const mocks = vi.hoisted(() => ({ load: vi.fn(), call: vi.fn() }));
vi.mock('@/lib/server/services/regional-evidence-tools', () => ({
  loadRegionalEvidenceTools: mocks.load, callRegionalEvidenceTool: mocks.call,
}));
import { boundedEvidence, evidenceResultStatus, prepareRegionalAnalysis, regionalEvidenceAuditCall, regionalEvidenceDay, regionalEvidenceStageStatus, STRATEGY_SCREENING } from '@/lib/server/services/regional-analysis-workflow';
import { reportWarehouseEvidenceIssues } from '@/lib/server/services/remediation-report';

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
  readings: [reading('climate-precipitation', '2024-06-15'), reading('climate-soil-wetness-root-zone', '2023-06-15'), reading('soil-moisture', '2022-06-15')],
  viewedDates: ['2022-06-15', '2023-06-15', '2024-06-15'], sourcesServedAsOfLatest: [],
};
const toolNames = ['surface_value_near_point', 'observation_coverage_on_day', 'drought_history_at_point', 'fire_history_near_point', 'observation_temporal_neighbors'];

beforeEach(() => {
  mocks.load.mockResolvedValue({ tools: toolNames.map((name) => ({ name, description: name, input_schema: {} })), surfaces: [...REGIONAL_TOOL_EVIDENCE_SOURCES], featureSurfaces: [], valueSurfaces: [] });
  mocks.call.mockResolvedValue(JSON.stringify({ features: [{ served_day: '2024-06-15', properties: { value: 0.4, unit: 'm3/m3' } }], day_state: { state: 'published' } }));
});
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });

describe('regional evidence graph', () => {
  it('attempts every catalogue surface before dated historical and explicit regional comparisons', async () => {
    const result = await prepareRegionalAnalysis(payload, temporal);
    expect(result.evidence.toolCalls.filter((call) => call.stage === 'local').map((call) => call.source).sort()).toEqual([...REGIONAL_TOOL_EVIDENCE_SOURCES].sort());
    expect(result.evidence.stages.map((stage) => stage.id)).toEqual(['inventory', 'local', 'temporal', 'regional', 'strategies']);
    const local = result.evidence.toolCalls.filter((call) => call.stage === 'local');
    expect(local.find((call) => call.source === 'climate-field-precipitation')?.selectedDate).toBe('2024-06-15');
    expect(local.find((call) => call.source === 'climate-field-soil-wetness-root-zone')?.selectedDate).toBe('2023-06-15');
    expect(local.find((call) => call.source === 'soil-field-moisture')?.selectedDate).toBe('2022-06-15');
    const past = result.evidence.toolCalls.filter((call) => call.stage === 'temporal');
    expect(past).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'climate-field-precipitation', selectedDate: '2023-06-15' }),
      expect.objectContaining({ source: 'climate-field-soil-wetness-root-zone', selectedDate: '2022-06-15' }),
      expect.objectContaining({ source: 'climate-field-soil-wetness-root-zone', selectedDate: '2018-06-15' }),
    ]));
    const regional = result.evidence.toolCalls.filter((call) => call.stage === 'regional');
    expect(new Set(regional.map((call) => call.location?.lon))).toEqual(new Set([-119.5, -116.5]));
    expect(regional.every((call) => call.selectedDate === (call.source === 'climate-field-precipitation' ? '2024-06-15' : '2023-06-15'))).toBe(true);
    expect(result.context).toContain('not validated ecological analogues');
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

  it('reserves historical and regional work when local reads exhaust their deadline', async () => {
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
    expect(result.evidence.toolCalls.some((call) => call.stage === 'local' && call.status === 'not_queried')).toBe(true);
    expect(result.evidence.toolCalls.some((call) => call.stage === 'temporal' && call.status === 'error')).toBe(true);
    expect(result.evidence.toolCalls.some((call) => call.stage === 'regional' && call.status === 'error')).toBe(true);
    expect(result.evidence.toolCalls.some((call) => call.status === 'observed')).toBe(false);
  });

  it('keeps failed catalogue discovery explicit without attempting any data tools', async () => {
    mocks.load.mockRejectedValue(new Error('unavailable'));
    const result = await prepareRegionalAnalysis(payload, temporal);
    expect(mocks.call).not.toHaveBeenCalled();
    expect(result.evidence.stages.every((stage) => stage.status === 'unavailable')).toBe(true);
    expect(result.evidence.limitations.join(' ')).toContain('historical and regional comparisons were not performed');
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
  it.each(['signal_summaries', 'weekly_severity', 'signals_on_day', 'temporal_neighbors', 'nearest_cells'])('recognizes real %s payload rows', (key) => {
    expect(evidenceResultStatus({ [key]: [{ observed_day: '2023-06-15', severity_class: null }] }).status).toBe('observed');
  });
  it('distinguishes empty history summaries, refusals and governed absence from observations', () => {
    expect(evidenceResultStatus({ layer_summaries: [{ feature_count: 0, row_count: 0 }] }).status).toBe('unavailable');
    expect(evidenceResultStatus({ layer_summaries: [{ feature_count: 2, row_count: 2 }] }).status).toBe('observed');
    expect(evidenceResultStatus({ error: 'parquet_availability_withheld' }).status).toBe('refused');
    expect(evidenceResultStatus({ features: [], day_state: { state: 'governed_absence' } }).status).toBe('governed_absence');
    expect(evidenceResultStatus({ lanes: [{ day_state: { state: 'published' }, features: [{}] }, { day_state: { state: 'lane_never_written' }, features: [] }] }).status).toBe('unavailable');
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
    expect(reportWarehouseEvidenceIssues(report, payload, { ...evidence, toolCalls: [{ ...evidence.toolCalls[0], status: 'observed' }] })).toEqual([]);
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
    for (const tool of ['observation_coverage_on_day', 'observation_temporal_neighbors', 'nearest_signal_cells']) {
      const metadata: RegionalAnalysisEvidence = { ...evidence, toolCalls: [{ ...evidence.toolCalls[0], tool, status: 'observed' }] };
      expect(reportWarehouseEvidenceIssues(refusedReference, payload, metadata).length).toBeGreaterThan(0);
      expect(reportWarehouseEvidenceIssues(report, payload, metadata)).toHaveLength(1);
    }
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
