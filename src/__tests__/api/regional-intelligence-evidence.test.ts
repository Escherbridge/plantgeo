import { afterEach, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

const mocks = vi.hoisted(() => ({ stream: vi.fn(), recordExchange: vi.fn() }));
vi.mock('@/lib/server/auth', () => ({ getServerSession: async () => ({ user: { id: 'owner' } }) }));
vi.mock('@/lib/server/services/regional-context', () => ({ assembleRegionalContext: async () => ({
  payload: { location: { lat: 44, lon: -116, geohash: '9r' } }, dataFreshness: {}, contextIsEmpty: true, temporalContext: {},
}) }));
vi.mock('@/lib/server/services/ai-prompt', () => ({ streamRegionalIntelligence: mocks.stream }));
vi.mock('@/lib/server/services/ai-conversations', () => ({
  openConversation: async () => ({ id: 'saved-conversation', history: [] }), recordExchange: mocks.recordExchange,
}));
vi.mock('@/lib/server/security/regional-intelligence-access', () => ({
  REGIONAL_INTELLIGENCE_SERVING_STATE: 'active', REGIONAL_INTELLIGENCE_INACTIVE_MESSAGE: 'Unavailable',
  reserveRegionalIntelligenceUsage: async () => ({ allowed: true, remaining: 1 }),
}));

import { POST } from '@/app/api/ai/regional-intelligence/route';

afterEach(() => { vi.unstubAllEnvs(); vi.clearAllMocks(); });

it('streams and persists validated server evidence while retaining the last valid audit', async () => {
  vi.stubEnv('OPENROUTER_API_KEY', 'test-key');
  const evidence = { version: 1, stages: [{ id: 'local', label: 'Local evidence', status: 'partial' }], toolCalls: [{ id: 'one', stage: 'local', tool: 'surface_values_near_point', source: 'soil-field-moisture', selectedDate: '2026-09-10', status: 'unavailable' }], limitations: ['No publication on the selected day.'] };
  const report = { riskSummary: { level: 'low', headline: 'Evidence is limited.', factors: [], evidenceOrigin: 'model_inference', evidenceSources: [] }, observations: [], remediation: [], professionalConsultation: 'Consult an agronomist.' };
  mocks.stream.mockImplementation(async function* () {
    yield { type: 'evidence', evidence };
    yield { type: 'evidence', evidence: { ...evidence, toolCalls: [{ ...evidence.toolCalls[0], selectedDate: 'yesterday' }] } };
    yield { type: 'report', report };
  });
  mocks.recordExchange.mockResolvedValue({ assistantMessageId: 'saved-answer' });
  const response = await POST(new NextRequest('https://plantgeo.test/api/ai/regional-intelligence', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lat: 44, lon: -116, locationConsent: { precision: 'approximate', confirmed: true } }),
  }));
  const text = await response.text();
  expect(text).toContain(`event: evidence\ndata: ${JSON.stringify(evidence)}`);
  expect(text).not.toContain('yesterday');
  expect(mocks.recordExchange).toHaveBeenCalledWith(expect.objectContaining({ structuredResponse: expect.objectContaining({ analysisEvidence: evidence }) }));
  const done = text.split('event: done\ndata: ')[1].split('\n')[0];
  expect(JSON.parse(done).analysisEvidence).toEqual(evidence);
});

it('rejects a legacy warehouse citation without its exact assembled payload block', async () => {
  vi.stubEnv('OPENROUTER_API_KEY', 'test-key');
  const evidence = { version: 1, stages: [], toolCalls: [{ id: 'one', stage: 'local', tool: 'surface_value_near_point', source: 'interventions', status: 'refused', reason: 'withheld' }], limitations: [] };
  const report = {
    riskSummary: { level: 'low', headline: 'Interventions are supported here.', factors: [], evidenceOrigin: 'warehouse', evidenceSources: ['strategyRecommendations'] },
    observations: [], remediation: [], professionalConsultation: 'Consult an agronomist.',
  };
  mocks.stream.mockImplementation(async function* () {
    yield { type: 'evidence', evidence };
    yield { type: 'report', report };
  });
  const response = await POST(new NextRequest('https://plantgeo.test/api/ai/regional-intelligence', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lat: 44, lon: -116, locationConsent: { precision: 'approximate', confirmed: true } }),
  }));
  const text = await response.text();
  expect(text).toContain('event: error');
  expect(text).not.toContain('event: done');
  expect(mocks.recordExchange).not.toHaveBeenCalled();
});

it('persists scoped references to an observed regional comparison', async () => {
  vi.stubEnv('OPENROUTER_API_KEY', 'test-key');
  const evidence = { version: 1, stages: [{ id: 'regional', label: 'Regional comparison', status: 'completed' }], toolCalls: [
    { id: 'regional-1', stage: 'regional', tool: 'surface_value_near_point', source: 'soil-field-moisture', selectedDate: '2024-05-01', observedDates: ['2024-05-01'], location: { lat: 44, lon: -117.5 }, status: 'observed' },
  ], limitations: [] };
  const report = {
    riskSummary: { level: 'low', headline: 'Local evidence is limited.', factors: [], evidenceOrigin: 'model_inference', evidenceSources: [] },
    observations: [{ statement: 'Soil moisture evidence was returned at the comparison point 44, -117.5 for 2024-05-01.', evidenceOrigin: 'warehouse', evidenceSource: 'soil-field-moisture', evidenceReadIds: ['regional-1'] }],
    remediation: [], professionalConsultation: 'Consult an agronomist.',
  };
  mocks.stream.mockImplementation(async function* () {
    yield { type: 'evidence', evidence };
    yield { type: 'report', report };
  });
  mocks.recordExchange.mockResolvedValue({ assistantMessageId: 'saved-answer' });
  const response = await POST(new NextRequest('https://plantgeo.test/api/ai/regional-intelligence', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lat: 44, lon: -116, locationConsent: { precision: 'approximate', confirmed: true } }),
  }));
  expect(await response.text()).toContain('event: done');
  expect(mocks.recordExchange).toHaveBeenCalledWith(expect.objectContaining({ structuredResponse: expect.objectContaining({ observations: report.observations, analysisEvidence: evidence }) }));
});
