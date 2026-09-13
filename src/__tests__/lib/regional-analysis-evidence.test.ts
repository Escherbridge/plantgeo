import { describe, expect, it } from 'vitest';
import { readRegionalAnalysisEvidence } from '@/lib/regional-analysis-evidence';
import { readSavedReport } from '@/lib/regional-intelligence-saved-report';
import { remediationReportSchema } from '@/lib/server/services/remediation-report';
import { REGIONAL_TOOL_EVIDENCE_SOURCES } from '@/lib/regional-intelligence';

const evidence = {
  version: 1,
  stages: [{ id: 'history', label: 'Historical comparison', status: 'partial' }],
  toolCalls: [{ id: 'one', stage: 'history', tool: 'surface_values_near_point', source: 'soil-field-moisture', selectedDate: '2025-09-10', location: { lat: 44, lon: -116 }, status: 'unavailable', reason: 'No publication for the selected day.' }],
  limitations: ['Historical moisture could not be established.'],
};

const report = {
  riskSummary: { level: 'moderate', headline: 'Evidence is limited.', factors: [], evidenceOrigin: 'model_inference', evidenceSources: [] },
  observations: [], remediation: [], professionalConsultation: 'Consult an agronomist.',
};

describe('regional analysis evidence boundary', () => {
  it('preserves server audit on saved reports and accepts older reports without an audit', () => {
    const saved = { ...report, aiGenerated: true, webSources: [], dataFreshness: {} };
    expect(readSavedReport('assistant', saved)).toEqual(saved);
    expect(readSavedReport('assistant', { ...saved, analysisEvidence: evidence })?.analysisEvidence).toEqual(evidence);
    expect(remediationReportSchema.safeParse({ ...report, analysisEvidence: evidence }).success).toBe(false);
  });

  it('validates historical calendar dates and coordinates instead of silently normalizing the audit', () => {
    expect(readRegionalAnalysisEvidence(evidence)).toEqual(evidence);
    for (const changed of [
      { selectedDate: '2025-02-30' },
      { selectedDate: 'yesterday' },
      { servedDates: ['2025-02-30'] },
      { location: { lat: 91, lon: -116 } },
      { status: 'confirmed_success' },
    ]) {
      expect(readRegionalAnalysisEvidence({ ...evidence, toolCalls: [{ ...evidence.toolCalls[0], ...changed }] })).toBeNull();
    }
  });

  it('persists the publication day separately from the requested analysis day', () => {
    const dated = { ...evidence, toolCalls: [{ ...evidence.toolCalls[0], servedDates: ['2025-09-01'] }] };
    const saved = { ...report, aiGenerated: true, webSources: [], dataFreshness: {}, analysisEvidence: dated };
    expect(readSavedReport('assistant', saved)?.analysisEvidence?.toolCalls[0].servedDates).toEqual(['2025-09-01']);
    expect(readSavedReport('assistant', saved)?.analysisEvidence?.toolCalls[0].selectedDate).toBe('2025-09-10');
  });

  it('allows reports to cite each governed surface without treating it as initial freshness', () => {
    for (const source of REGIONAL_TOOL_EVIDENCE_SOURCES) {
      expect(remediationReportSchema.safeParse({
        ...report,
        riskSummary: { ...report.riskSummary, evidenceSources: [source] },
        observations: [{ statement: 'The tool returned evidence for the requested day.', evidenceOrigin: 'warehouse', evidenceSource: source }],
      }).success).toBe(true);
    }
    expect(remediationReportSchema.safeParse({ ...report, riskSummary: { ...report.riskSummary, evidenceSources: ['invented-surface'] } }).success).toBe(false);
  });

  it('allows read IDs only on warehouse-origin claims and trims every supplied ID', () => {
    for (const evidenceOrigin of ['web', 'model_inference'] as const) {
      expect(remediationReportSchema.safeParse({
        ...report,
        riskSummary: { ...report.riskSummary, evidenceOrigin, evidenceReadIds: ['one'] },
      }).success).toBe(false);
      expect(remediationReportSchema.safeParse({
        ...report,
        observations: [{ statement: 'Inference.', evidenceOrigin, evidenceReadIds: [] }],
      }).success).toBe(false);
    }
    const parsed = remediationReportSchema.parse({
      ...report,
      riskSummary: { ...report.riskSummary, evidenceOrigin: 'warehouse', evidenceSources: ['vegetation'], evidenceReadIds: ['  regional-1  '] },
    });
    expect(parsed.riskSummary.evidenceReadIds).toEqual(['regional-1']);
    for (const evidenceReadIds of [null, ['   '], ['x'.repeat(101)]]) {
      expect(remediationReportSchema.safeParse({
        ...report,
        riskSummary: { ...report.riskSummary, evidenceOrigin: 'warehouse', evidenceSources: ['vegetation'], evidenceReadIds },
      }).success).toBe(false);
    }
  });

  it('retains truthful multi-source provenance for combined evidence reads', () => {
    const combined = { ...evidence, toolCalls: [{
      id: 'fire-history', stage: 'temporal', tool: 'fire_history_near_point',
      sources: ['burn-severity', 'fire-detections'], status: 'observed',
    }] };
    expect(readRegionalAnalysisEvidence(combined)).toEqual(combined);
    expect(readRegionalAnalysisEvidence({ ...combined, toolCalls: [{ ...combined.toolCalls[0], sources: ['burn-severity'] }] })).toBeNull();
  });
});
