import { z } from 'zod/v4';
import {
  EVIDENCE_ORIGINS,
  INTERVENTION_STRATEGIES,
  PROFESSIONAL_DISCIPLINES,
  REGIONAL_CLAIM_EVIDENCE_SOURCES,
  isRegionalEvidenceSource,
  type RegionalAnalysisEvidence,
  type RegionalClaimEvidenceSource,
} from '@/lib/regional-intelligence';
import type { RegionalContextPayload } from './regional-context';

const evidenceReadIdsSchema = z.array(z.string().trim().min(1).max(100)).max(8).optional()
  .describe('IDs of executed evidence reads supporting this claim. Required for historical, regional or additional tool evidence; their dates and locations are shown beside the claim.');

function warehouseReadIdsOnly(
  value: { evidenceOrigin: string; evidenceReadIds?: string[] },
  context: z.RefinementCtx,
): void {
  if (value.evidenceOrigin !== 'warehouse' && value.evidenceReadIds !== undefined) {
    context.addIssue({
      code: 'custom', path: ['evidenceReadIds'],
      message: 'evidenceReadIds are allowed only on warehouse-origin claims.',
    });
  }
}

const riskSummarySchema = z.object({
  level: z.enum(['low', 'moderate', 'high', 'critical']),
  headline: z.string().trim().min(1).max(300),
  factors: z.array(z.string().trim().min(1).max(240)).max(8),
  evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
  evidenceSources: z.array(z.enum(REGIONAL_CLAIM_EVIDENCE_SOURCES)).max(REGIONAL_CLAIM_EVIDENCE_SOURCES.length),
  evidenceReadIds: evidenceReadIdsSchema,
}).strict().superRefine(warehouseReadIdsOnly);

const observationSchema = z.object({
  statement: z.string().trim().min(1).max(500),
  evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
  evidenceSource: z.enum(REGIONAL_CLAIM_EVIDENCE_SOURCES).optional(),
  evidenceReadIds: evidenceReadIdsSchema,
}).strict().superRefine(warehouseReadIdsOnly);

const remediationRecommendationSchema = z.object({
  strategy: z.enum(INTERVENTION_STRATEGIES),
  title: z.string().trim().min(1).max(160),
  rationale: z.string().trim().min(1).max(900),
  timeframe: z.enum(['immediate', 'short_term', 'long_term']),
  confidence: z.enum(['low', 'moderate', 'high']),
  consultProfessionals: z.array(z.enum(PROFESSIONAL_DISCIPLINES)).max(5),
  evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
  evidenceSource: z.enum(REGIONAL_CLAIM_EVIDENCE_SOURCES).optional(),
  evidenceReadIds: evidenceReadIdsSchema,
}).strict().superRefine(warehouseReadIdsOnly);

/** Canonical report contract shared by provider tools, the agent loop and the route. */
export const remediationReportSchema = z
  .object({
    riskSummary: riskSummarySchema,
    observations: z.array(observationSchema).max(12),
    remediation: z.array(remediationRecommendationSchema).max(8),
    professionalConsultation: z.string().trim().min(1).max(600)
      .describe('One short sentence naming the relevant professional disciplines to consult before acting; aim below 200 characters. Do not repeat disclaimers, evidence, strategy rationales or per-strategy explanations.'),
  })
  .strict();

/** Model-visible constraints generated from the exact runtime validator. */
export const REMEDIATION_REPORT_JSON_SCHEMA = z.toJSONSchema(remediationReportSchema, {
  target: 'draft-7',
});

export type RemediationReport = z.infer<typeof remediationReportSchema>;

type ReportEvidenceIssue = { code: 'custom'; path: (string | number)[]; message: string };

function isMeasurementRead(call: RegionalAnalysisEvidence['toolCalls'][number]): boolean {
  return call.status === 'observed' && ![
    'observation_coverage_on_day', 'observation_temporal_neighbors', 'nearest_signal_cells',
  ].includes(call.tool);
}

function auditSources(call: RegionalAnalysisEvidence['toolCalls'][number]): string[] {
  return call.sources ?? (call.source ? [call.source] : []);
}

function payloadSupportsWarehouseClaim(
  source: RegionalClaimEvidenceSource,
  payload: RegionalContextPayload,
  dataFreshness: Record<string, string>,
): boolean {
  switch (source) {
    case 'drought': return payload.waterScarcity != null && (
      payload.waterScarcity.droughtClass != null || Number.isFinite(Date.parse(dataFreshness.drought ?? ''))
    );
    case 'streamflow': return payload.waterScarcity?.nearestGauge != null;
    case 'weatherObservations': return payload.weather != null;
    case 'fireDetections': return payload.fireDetections != null;
    case 'firePerimeters': return payload.firePerimeters != null;
    case 'mtbsPerimeters': return payload.mtbsPerimeters != null;
    case 'strategyRecommendations': return (payload.strategyRecommendations?.length ?? 0) > 0;
    case 'soilProperties': return payload.soilProperties != null;
    case 'carbonPotential': return payload.carbonPotential != null;
    default: return false;
  }
}

function toolAuditSupportsWarehouseClaim(
  source: RegionalClaimEvidenceSource,
  evidence: RegionalAnalysisEvidence | undefined,
  readIds: string[],
): boolean {
  return evidence?.toolCalls.some((call) =>
    auditSources(call).includes(source) && isMeasurementRead(call)
    && (readIds.length > 0 ? readIds.includes(call.id) : call.stage === 'local')
  ) ?? false;
}

/** Rejects warehouse labels that no admissible executed evidence read supports. */
export function reportWarehouseEvidenceIssues(
  report: RemediationReport,
  payload: RegionalContextPayload,
  evidence: RegionalAnalysisEvidence | undefined,
  dataFreshness: Record<string, string> = {},
): ReportEvidenceIssue[] {
  const claims: Array<{
    source: RegionalClaimEvidenceSource | undefined;
    path: (string | number)[];
    readIds: string[];
  }> = [];
  const referenceIssues: ReportEvidenceIssue[] = [];
  const verifyReferences = (
    readIds: string[] | undefined,
    sources: readonly RegionalClaimEvidenceSource[],
    origin: string,
    path: (string | number)[],
  ) => {
    if (readIds !== undefined && origin !== 'warehouse') {
      referenceIssues.push({ code: 'custom', path: [...path, 'evidenceReadIds'], message: 'Evidence read IDs are allowed only on warehouse-origin claims.' });
      return;
    }
    const toolSources = sources.filter((source) => !isRegionalEvidenceSource(source));
    for (const [index, id] of (readIds ?? []).entries()) {
      const call = evidence?.toolCalls.find((entry) => entry.id === id);
      if (!call || !isMeasurementRead(call) || !auditSources(call).some((source) => toolSources.includes(source as RegionalClaimEvidenceSource))) {
        referenceIssues.push({ code: 'custom', path: [...path, 'evidenceReadIds', index], message: `Evidence read ${id} is not an observed measurement for one of this claim's exact tool-surface citations. Legacy payload sources, coverage dates and reporting-cell metadata cannot be linked through a tool read ID.` });
      }
    }
  };
  verifyReferences(report.riskSummary.evidenceReadIds, report.riskSummary.evidenceSources, report.riskSummary.evidenceOrigin, ['riskSummary']);
  if (report.riskSummary.evidenceOrigin === 'warehouse') {
    const readIds = report.riskSummary.evidenceReadIds ?? [];
    if (report.riskSummary.evidenceSources.length === 0) {
      claims.push({ source: undefined, readIds, path: ['riskSummary', 'evidenceSources'] });
    } else {
      report.riskSummary.evidenceSources.forEach((source, index) => {
        claims.push({ source, readIds, path: ['riskSummary', 'evidenceSources', index] });
      });
    }
  }
  report.observations.forEach((claim, index) => {
    verifyReferences(claim.evidenceReadIds, claim.evidenceSource ? [claim.evidenceSource] : [], claim.evidenceOrigin, ['observations', index]);
    if (claim.evidenceOrigin === 'warehouse') {
      claims.push({ source: claim.evidenceSource, readIds: claim.evidenceReadIds ?? [], path: ['observations', index, 'evidenceSource'] });
    }
  });
  report.remediation.forEach((claim, index) => {
    verifyReferences(claim.evidenceReadIds, claim.evidenceSource ? [claim.evidenceSource] : [], claim.evidenceOrigin, ['remediation', index]);
    if (claim.evidenceOrigin === 'warehouse') {
      claims.push({ source: claim.evidenceSource, readIds: claim.evidenceReadIds ?? [], path: ['remediation', index, 'evidenceSource'] });
    }
  });
  return [...referenceIssues, ...claims.flatMap(({ source, path, readIds }) => {
    const supported = source && (isRegionalEvidenceSource(source)
      ? payloadSupportsWarehouseClaim(source, payload, dataFreshness)
      : toolAuditSupportsWarehouseClaim(source, evidence, readIds));
    if (supported) return [];
    return [{
      code: 'custom' as const,
      path,
      message: source
        ? `Warehouse citation ${source} is unsupported. Legacy sources require their exact assembled payload block. Tool surfaces require an observed read of that exact source: cite its evidenceReadIds for historical, regional or additional evidence so the actual scope is displayed. Without references only an observed local-stage read supports the citation. Refused, failed, unavailable and skipped reads cannot support a warehouse citation.`
        : 'A warehouse claim must name the evidence source supported by an admissible executed read.',
    }];
  })];
}
