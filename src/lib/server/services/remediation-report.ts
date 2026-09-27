import { z } from 'zod/v4';
import {
  EVIDENCE_ORIGINS,
  INTERVENTION_STRATEGIES,
  LITERATURE_DIRECTIONS,
  PROFESSIONAL_DISCIPLINES,
  REGIONAL_CLAIM_EVIDENCE_SOURCES,
  REGIONAL_EVIDENCE_SOURCES,
  STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE,
  isRegionalEvidenceSource,
  isStrategyKnowledgeTool,
  type LiteratureCitation,
  type LiteratureDirection,
  type RegionalAnalysisEvidence,
  type RegionalClaimEvidenceSource,
} from '@/lib/regional-intelligence';
import type { RegionalContextPayload } from './regional-context';
import type { RegionalMeasurementFact } from './regional-measurement-facts';

const evidenceReadIdsSchema = z.array(z.string().trim().min(1).max(100)).max(8).optional()
  .describe('REQUIRED and nonempty for EVERY warehouse claim citing a tool surface, including local reads. Copy the current manifest IDs matching each exact source. Legacy payload sources alone do not use these IDs. Omit on inference, web and literature claims.');

function warehouseReadIdsOnly(
  value: { evidenceOrigin: string; evidenceReadIds?: string[]; evidenceSource?: string; evidenceSources?: string[] },
  context: z.RefinementCtx,
): void {
  if (value.evidenceOrigin !== 'warehouse' && value.evidenceReadIds !== undefined) {
    context.addIssue({
      code: 'custom', path: ['evidenceReadIds'],
      message: 'evidenceReadIds are allowed only on warehouse-origin claims.',
    });
  }
  const sources = value.evidenceSources ?? (value.evidenceSource ? [value.evidenceSource] : []);
  if (value.evidenceOrigin === 'warehouse' && sources.some((source) => !isRegionalEvidenceSource(source))
    && !value.evidenceReadIds?.length) {
    context.addIssue({
      code: 'custom', path: ['evidenceReadIds'],
      message: 'Every tool-surface warehouse claim requires nonempty evidenceReadIds matching each cited source, including local measurements.',
    });
  }
}

const LITERATURE_RECORD_IDS_DESCRIPTION = 'Only with evidenceOrigin "literature": the finding_id and/or strategy_id values of the records this claim restates, copied exactly from this turn\'s literature results. The server attaches each record\'s magnitude, direction, conditions and source; never write them yourself.';

const literatureRecordIdsSchema = z.array(z.string().trim().min(1).max(200)).max(8).optional()
  .describe(LITERATURE_RECORD_IDS_DESCRIPTION);

/** A server-written citation; the model never supplies these fields (see `pairLiteratureProvenance`). */
const literatureCitationSchema = z.object({
  recordId: z.string().trim().min(1).max(200),
  kind: z.enum(['finding', 'strategy']),
  title: z.string().trim().min(1).max(300),
  magnitude: z.string().trim().min(1).max(200).optional(),
  direction: z.enum(LITERATURE_DIRECTIONS).optional(),
  conditions: z.string().trim().min(1).max(600).optional(),
  sourceUrl: z.url().max(2_048).refine((value) => value.startsWith('https://'), 'Literature source links must be https.').optional(),
}).strict();

/** Server-written literature fields, stripped from the model-visible report schema. */
const SERVER_OWNED_CLAIM_FIELDS = ['literatureCitations', 'groundingNote'] as const;

const literatureClaimFields = {
  literatureRecordIds: literatureRecordIdsSchema,
  literatureCitations: z.array(literatureCitationSchema).max(8).optional(),
  groundingNote: z.string().trim().min(1).max(240).optional(),
};

/** Record ids and citations belong to literature claims; a grounding note only to a downgraded (model_inference) claim. */
function literatureFieldsPaired(
  value: { evidenceOrigin: string; literatureRecordIds?: string[]; literatureCitations?: unknown[]; groundingNote?: string },
  context: z.RefinementCtx,
): void {
  for (const field of ['literatureRecordIds', 'literatureCitations'] as const) {
    if (value[field] !== undefined && value.evidenceOrigin !== 'literature') {
      context.addIssue({ code: 'custom', path: [field], message: `${field} are allowed only on literature-origin claims.` });
    }
  }
  if (value.groundingNote !== undefined && value.evidenceOrigin !== 'model_inference') {
    context.addIssue({ code: 'custom', path: ['groundingNote'], message: 'A grounding note marks only a literature claim downgraded to model_inference.' });
  }
}

/**
 * Claim sources for the two render paths whose badge ignores the source (strategy chips and the
 * riskSummary export line): SoilGrids model estimates are excluded until the panel's h7 fix lands,
 * so no estimate can wear "Observed data". See soil/AGENTS.md §badge-interim-filter.
 */
const BADGE_BLIND_CLAIM_EVIDENCE_SOURCES = REGIONAL_CLAIM_EVIDENCE_SOURCES.filter(
  (source): source is Exclude<RegionalClaimEvidenceSource, 'soilProperties'> => source !== 'soilProperties',
);

const riskSummarySchema = z.object({
  level: z.enum(['low', 'moderate', 'high', 'critical']),
  headline: z.string().trim().min(1).max(300),
  factors: z.array(z.string().trim().min(1).max(240)).max(8),
  evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
  evidenceSources: z.array(z.enum(BADGE_BLIND_CLAIM_EVIDENCE_SOURCES)).max(REGIONAL_CLAIM_EVIDENCE_SOURCES.length),
  evidenceReadIds: evidenceReadIdsSchema,
}).strict().superRefine(warehouseReadIdsOnly);

const observationSchema = z.object({
  statement: z.string().trim().min(1).max(500),
  evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
  evidenceSource: z.enum(REGIONAL_CLAIM_EVIDENCE_SOURCES).optional(),
  evidenceReadIds: evidenceReadIdsSchema,
  ...literatureClaimFields,
}).strict().superRefine(warehouseReadIdsOnly).superRefine(literatureFieldsPaired);

const remediationRecommendationSchema = z.object({
  strategy: z.enum(INTERVENTION_STRATEGIES),
  title: z.string().trim().min(1).max(160),
  rationale: z.string().trim().min(1).max(900),
  timeframe: z.enum(['immediate', 'short_term', 'long_term']),
  confidence: z.enum(['low', 'moderate', 'high']),
  consultProfessionals: z.array(z.enum(PROFESSIONAL_DISCIPLINES)).max(5),
  evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
  evidenceSource: z.enum(BADGE_BLIND_CLAIM_EVIDENCE_SOURCES).optional(),
  evidenceReadIds: evidenceReadIdsSchema,
  ...literatureClaimFields,
}).strict().superRefine(warehouseReadIdsOnly).superRefine(literatureFieldsPaired);

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

/** Remove server-written claim fields from a JSON schema so the model is never offered them. */
function withoutServerOwnedFields(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(withoutServerOwnedFields);
  if (!node || typeof node !== 'object') return node;
  const result: Record<string, unknown> = Object.fromEntries(Object.entries(node).map(([key, value]) => [key, withoutServerOwnedFields(value)]));
  const properties = result.properties as Record<string, unknown> | undefined;
  if (properties && SERVER_OWNED_CLAIM_FIELDS.some((field) => field in properties)) {
    result.properties = Object.fromEntries(Object.entries(properties)
      .filter(([key]) => !(SERVER_OWNED_CLAIM_FIELDS as readonly string[]).includes(key)));
    if (Array.isArray(result.required)) result.required = result.required.filter((key) => !(SERVER_OWNED_CLAIM_FIELDS as readonly unknown[]).includes(key));
  }
  return result;
}

/** Model-visible constraints generated from the exact runtime validator, minus server-written fields. */
export const REMEDIATION_REPORT_JSON_SCHEMA = withoutServerOwnedFields(z.toJSONSchema(remediationReportSchema, {
  target: 'draft-7',
})) as Record<string, unknown>;

export type RemediationReport = z.infer<typeof remediationReportSchema>;

type ReportEvidenceIssue = { code: 'custom'; path: (string | number)[]; message: string };

function isMeasurementRead(call: RegionalAnalysisEvidence['toolCalls'][number]): boolean {
  return call.status === 'observed' && !isStrategyKnowledgeTool(call.tool) && ![
    'observation_coverage_on_day', 'observation_temporal_neighbors', 'list_environmental_layers',
  ].includes(call.tool);
}

/** Whether a strategy-knowledge tool answered (not refused) this turn; gates the literature origin. */
export function strategyKnowledgeAnswered(evidence: RegionalAnalysisEvidence | undefined): boolean {
  return evidence?.toolCalls.some((call) => isStrategyKnowledgeTool(call.tool) && call.status === 'answered') ?? false;
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
    && readIds.includes(call.id)
  ) ?? false;
}

/** Report citations admitted by the same payload and read predicates as validation. */
export function reportCitationManifest(
  payload: RegionalContextPayload,
  evidence: RegionalAnalysisEvidence | undefined,
  dataFreshness: Record<string, string> = {},
) {
  return {
    payloadSources: REGIONAL_EVIDENCE_SOURCES.filter((source) => payloadSupportsWarehouseClaim(source, payload, dataFreshness)),
    measurementReads: (evidence?.toolCalls ?? []).filter(isMeasurementRead).flatMap((call) =>
      auditSources(call).filter((source): source is RegionalClaimEvidenceSource =>
        !isRegionalEvidenceSource(source)
        && (REGIONAL_CLAIM_EVIDENCE_SOURCES as readonly string[]).includes(source)
      ).map((evidenceSource) => ({
        evidenceSource,
        evidenceReadId: call.id,
        stage: call.stage,
        selectedDate: call.selectedDate,
        rangeStart: call.rangeStart,
        rangeEnd: call.rangeEnd,
        servedDates: call.servedDates,
        observedDates: call.observedDates,
      }))
    ),
  };
}

/**
 * Narrow provider choices to this turn's actual evidence without weakening the validator.
 * `literatureAnswered` (see `strategyKnowledgeAnswered`) is the only way the literature origin is offered;
 * `literatureRecordIds` (see `literatureRecordsFromResult`) narrows the citable record ids when given.
 */
export function reportSchemaForCitations(
  manifest: ReturnType<typeof reportCitationManifest>,
  measurementFacts?: readonly RegionalMeasurementFact[],
  { literatureAnswered = false, literatureRecordIds }: { literatureAnswered?: boolean; literatureRecordIds?: readonly string[] } = {},
): Record<string, unknown> {
  const sources = [...new Set([
    ...manifest.payloadSources,
    ...manifest.measurementReads.map((read) => read.evidenceSource),
  ])];
  const readIds = [...new Set(manifest.measurementReads.map((read) => read.evidenceReadId))];
  const visit = (node: unknown): unknown => {
    if (Array.isArray(node)) return node.map(visit);
    if (!node || typeof node !== 'object') return node;
    const result: Record<string, unknown> = Object.fromEntries(Object.entries(node).map(([key, value]) => [key, visit(value)]));
    const properties = result.properties as Record<string, Record<string, unknown>> | undefined;
    if (!properties) return result;
    if (properties.literatureRecordIds) {
      if (!literatureAnswered) delete properties.literatureRecordIds;
      else properties.literatureRecordIds = {
        ...properties.literatureRecordIds,
        description: LITERATURE_RECORD_IDS_DESCRIPTION,
        ...(literatureRecordIds?.length ? {
          items: { ...properties.literatureRecordIds.items as Record<string, unknown>, enum: [...new Set(literatureRecordIds)] },
        } : {}),
      };
    }
    if (properties.observations && (measurementFacts === undefined ? readIds.length > 0 : measurementFacts.length > 0)) {
      properties.observations.minItems = 1;
      properties.observations.description = 'Measurements were returned. Include at least one warehouse observation grounded in an exact current source/read pair. Historical gaps do not erase available measurements.';
    }
    if (properties.evidenceOrigin && sources.length === 0) {
      properties.evidenceOrigin.enum = EVIDENCE_ORIGINS.filter((origin) => origin !== 'warehouse'
        && (literatureAnswered || origin !== 'literature'));
    }
    if (properties.evidenceSources) {
      properties.evidenceOrigin.enum = ['model_inference'];
      properties.evidenceSources.items = { type: 'string' };
      properties.evidenceSources.maxItems = 0;
      properties.evidenceSources.description = 'Return []. Risk level, headline and factors are model interpretation; cite supporting measurements separately in observations.';
      delete properties.evidenceReadIds;
      result.required = (Array.isArray(result.required) ? result.required : []).filter((key) => key !== 'evidenceReadIds');
      return result;
    }
    // Recommendations are model interpretation (web/model_inference), plus literature only when a
    // strategy-knowledge tool answered this turn; none carries a warehouse read ID. Checked ahead of
    // the generic evidenceSource narrowing below, which would otherwise drop the literature source.
    if (properties.strategy && properties.rationale && properties.evidenceOrigin) {
      properties.evidenceOrigin.enum = literatureAnswered ? ['web', 'model_inference', 'literature'] : ['web', 'model_inference'];
      if (literatureAnswered && properties.evidenceSource) {
        properties.evidenceSource.enum = [STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE];
        properties.evidenceSource.description = 'Only with evidenceOrigin "literature"; omit for web and model_inference.';
      } else delete properties.evidenceSource;
      delete properties.evidenceReadIds;
      result.required = (Array.isArray(result.required) ? result.required : [])
        .filter((key) => key !== 'evidenceReadIds' && key !== 'evidenceSource');
      return result;
    }
    if (properties.evidenceSource) {
      if (sources.length === 0) delete properties.evidenceSource;
      else properties.evidenceSource.enum = sources;
    }
    if (measurementFacts !== undefined && properties.statement && properties.evidenceOrigin) {
      const inference = {
        ...result,
        properties: { statement: properties.statement, evidenceOrigin: { type: 'string', enum: ['web', 'model_inference'] } },
        required: ['statement', 'evidenceOrigin'], additionalProperties: false,
      };
      return measurementFacts.length > 0 ? { anyOf: [{
        type: 'object', additionalProperties: false,
        properties: {
          evidenceOrigin: { type: 'string', enum: ['warehouse'] },
          measurementFactId: { type: 'string', enum: measurementFacts.map((fact) => fact.id), description: 'Select one current server-authored measurement fact. The server supplies its exact statement, source and read citations; do not add or rewrite them.' },
        },
        required: ['evidenceOrigin', 'measurementFactId'],
      }, inference] } : inference;
    }
    if (properties.evidenceReadIds) {
      if (readIds.length === 0) delete properties.evidenceReadIds;
      else {
        properties.evidenceReadIds.items = { ...properties.evidenceReadIds.items as Record<string, unknown>, enum: readIds };
        properties.evidenceReadIds.description = 'Required for warehouse tool-surface claims: nonempty executed IDs matching each cited source in the current manifest. Include every read used for a comparison. Never invent IDs.';
        const required = Array.isArray(result.required) ? result.required : [];
        const citationBranch = (origins: string[], measured: boolean, payloadOnly = false, measuredSource?: string) => {
          const branchProperties: Record<string, Record<string, unknown>> = { ...properties, evidenceOrigin: { ...properties.evidenceOrigin, enum: origins } };
          // No citation branch offers the literature origin, so none carries record ids.
          delete branchProperties.literatureRecordIds;
          if (measured) {
            const sourceReadIds = measuredSource
              ? [...new Set(manifest.measurementReads.filter((read) => read.evidenceSource === measuredSource).map((read) => read.evidenceReadId))]
              : readIds;
            branchProperties.evidenceReadIds = { ...properties.evidenceReadIds, items: { ...properties.evidenceReadIds.items as Record<string, unknown>, enum: sourceReadIds }, minItems: 1 };
            if (measuredSource) {
              branchProperties.evidenceSource = { ...properties.evidenceSource, enum: [measuredSource] };
              if (properties.statement) branchProperties.statement = {
                ...properties.statement,
                description: `Describe only measurements returned by ${measuredSource}, using this source's reads (${sourceReadIds.join(', ')}), units and actual observation dates. Do not describe another source, missing data, unavailable dates or whole-window absence. Availability belongs in the server evidence limitations. Cite every read used for a dated comparison.`,
              };
            }
          }
          else {
            delete branchProperties.evidenceReadIds;
            if (!payloadOnly) delete branchProperties.evidenceSource;
          }
          if (payloadOnly && properties.evidenceSource) branchProperties.evidenceSource = { ...properties.evidenceSource, enum: manifest.payloadSources };
          if (payloadOnly && properties.evidenceSources) branchProperties.evidenceSources = { ...properties.evidenceSources, items: { type: 'string', enum: manifest.payloadSources } };
          return {
            ...result,
            properties: branchProperties,
            required: measured ? [...new Set([...required, ...(measuredSource ? ['evidenceSource'] : []), 'evidenceReadIds'])] : required.filter((key) => key !== 'evidenceReadIds'),
            additionalProperties: false,
          };
        };
        return {
          anyOf: [
            ...(properties.evidenceSource
              ? [...new Set(manifest.measurementReads.map((read) => read.evidenceSource))].map((source) => citationBranch(['warehouse'], true, false, source))
              : [citationBranch(['warehouse'], true)]),
            citationBranch(['web', 'model_inference'], false),
            ...(manifest.payloadSources.length ? [citationBranch(['warehouse'], false, true)] : []),
          ],
        };
      }
    }
    return result;
  };
  return visit(REMEDIATION_REPORT_JSON_SCHEMA) as Record<string, unknown>;
}

/**
 * Resolve only exact current fact selectors; supplied warehouse prose is never repaired.
 * The literature origin is admitted only when `evidence` records an answered strategy-knowledge call.
 */
export function resolveProviderMeasurementReport(
  input: Record<string, unknown> | null,
  facts: readonly RegionalMeasurementFact[],
  evidence?: RegionalAnalysisEvidence,
): {
  report: Record<string, unknown> | null; issues: ReportEvidenceIssue[];
} {
  const issues: ReportEvidenceIssue[] = [];
  if (!input || !Array.isArray(input.observations)) return { report: input, issues };
  const literatureAnswered = strategyKnowledgeAnswered(evidence);
  const validateInterpretation = (claim: unknown, path: (string | number)[], risk = false) => {
    if (!claim || typeof claim !== 'object' || Array.isArray(claim)) return;
    const fields = claim as Record<string, unknown>;
    const origin = String(fields.evidenceOrigin);
    // Literature is a nonwarehouse origin too (never a warehouse read ID), but unlike web/
    // model_inference it names its one fixed evidenceSource; risk stays model_inference-only.
    const allowed = risk ? ['model_inference'] : ['model_inference', 'web', 'literature'];
    if (!allowed.includes(origin)) issues.push({ code: 'custom', path: [...path, 'evidenceOrigin'], message: risk ? 'Risk assessment must be model_inference. Select supporting measured facts in observations.' : 'Recommendations and nonwarehouse observations must be model_inference, web or literature; select measured facts separately.' });
    if (Object.hasOwn(fields, 'evidenceReadIds')) issues.push({ code: 'custom', path: [...path, 'evidenceReadIds'], message: 'Interpretations cannot carry warehouse read-ID fields. Select supporting measurements separately by measurementFactId.' });
    if (!risk && origin === 'literature') {
      if (!literatureAnswered) issues.push({ code: 'custom', path: [...path, 'evidenceOrigin'], message: 'No strategy-knowledge literature tool answered in this analysis, so evidenceOrigin "literature" is unsupported. Relabel the claim model_inference or web, or remove it.' });
      if (fields.evidenceSource !== STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE) issues.push({ code: 'custom', path: [...path, 'evidenceSource'], message: 'A literature-origin claim requires evidenceSource "strategy-knowledge".' });
    } else if (Object.hasOwn(fields, 'evidenceSource')) {
      issues.push({ code: 'custom', path: [...path, 'evidenceSource'], message: 'Interpretations cannot carry a warehouse source field. Select supporting measurements separately by measurementFactId.' });
    }
    if (risk && Array.isArray(fields.evidenceSources) && fields.evidenceSources.length > 0) issues.push({ code: 'custom', path: [...path, 'evidenceSources'], message: 'Risk interpretation requires evidenceSources: []; supporting sources belong to selected measurement facts.' });
  };
  validateInterpretation(input.riskSummary, ['riskSummary'], true);
  if (Array.isArray(input.remediation)) input.remediation.forEach((claim, index) => validateInterpretation(claim, ['remediation', index]));
  const current = new Map(facts.map((fact) => [fact.id, fact]));
  const observations = input.observations.map((claim: unknown, index: number) => {
    if (!claim || typeof claim !== 'object' || Array.isArray(claim)) return claim;
    const fields = claim as Record<string, unknown>;
    if (fields.evidenceOrigin !== 'warehouse') {
      validateInterpretation(claim, ['observations', index]);
      return claim;
    }
    const fact = typeof fields.measurementFactId === 'string' ? current.get(fields.measurementFactId) : undefined;
    if (!fact || Object.keys(fields).some((key) => !['evidenceOrigin', 'measurementFactId'].includes(key))) {
      issues.push({ code: 'custom', path: ['observations', index, 'measurementFactId'], message: 'Warehouse observations must contain only evidenceOrigin:"warehouse" and one exact current measurementFactId. Unknown IDs and supplied statement/source/read IDs are rejected. Select an available fact without rewriting it.' });
      return claim;
    }
    return { statement: fact.statement, evidenceOrigin: 'warehouse', evidenceSource: fact.source, evidenceReadIds: [...fact.evidenceReadIds] };
  });
  return { report: { ...input, observations }, issues };
}

/** Translate only empty nonwarehouse citation arrays from the provider transport. */
export function normalizeProviderReport(input: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!input) return input;
  const normalizeClaim = (claim: unknown): unknown => {
    if (!claim || typeof claim !== 'object' || Array.isArray(claim)) return claim;
    const value = claim as Record<string, unknown>;
    if (!Object.hasOwn(value, 'evidenceReadIds')
      || (value.evidenceOrigin !== 'model_inference' && value.evidenceOrigin !== 'web')
      || !Array.isArray(value.evidenceReadIds) || value.evidenceReadIds.length !== 0) return claim;
    const normalized = { ...value };
    delete normalized.evidenceReadIds;
    return normalized;
  };
  return {
    ...input,
    riskSummary: normalizeClaim(input.riskSummary),
    observations: Array.isArray(input.observations) ? input.observations.map(normalizeClaim) : input.observations,
    remediation: Array.isArray(input.remediation) ? input.remediation.map(normalizeClaim) : input.remediation,
  };
}

/**
 * Pair the literature origin with its one fixed source before validation: fill a missing
 * "strategy-knowledge" on literature claims and strip it from web/model_inference claims.
 * Also drops model-written `literatureCitations`/`groundingNote` (server-owned) everywhere and
 * `literatureRecordIds` from non-literature claims. Never changes an origin, never touches read
 * IDs, and leaves any other conflicting source for the validator to reject. Runs ahead of
 * `groundLiteratureClaims` and `resolveProviderMeasurementReport`.
 */
export function pairLiteratureProvenance(input: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!input) return input;
  const pair = (claim: unknown): unknown => {
    if (!claim || typeof claim !== 'object' || Array.isArray(claim)) return claim;
    let value = claim as Record<string, unknown>;
    const strayFields = [...SERVER_OWNED_CLAIM_FIELDS, ...(value.evidenceOrigin === 'literature' ? [] : ['literatureRecordIds'])]
      .filter((field) => Object.hasOwn(value, field));
    if (strayFields.length) {
      value = { ...value };
      for (const field of strayFields) delete value[field];
    }
    if (value.evidenceOrigin === 'literature' && [undefined, null, ''].includes(value.evidenceSource as string | null | undefined)) {
      return { ...value, evidenceSource: STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE };
    }
    if ((value.evidenceOrigin === 'web' || value.evidenceOrigin === 'model_inference')
      && value.evidenceSource === STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE) {
      const paired = { ...value };
      delete paired.evidenceSource;
      return paired;
    }
    return value;
  };
  return {
    ...input,
    observations: Array.isArray(input.observations) ? input.observations.map(pair) : input.observations,
    remediation: Array.isArray(input.remediation) ? input.remediation.map(pair) : input.remediation,
  };
}

/** Rejects warehouse labels that no admissible executed evidence read supports. */
export function reportWarehouseEvidenceIssues(
  report: RemediationReport,
  payload: RegionalContextPayload,
  evidence: RegionalAnalysisEvidence | undefined,
  dataFreshness: Record<string, string> = {},
  measurementFacts?: readonly RegionalMeasurementFact[],
): ReportEvidenceIssue[] {
  const claims: Array<{
    source: RegionalClaimEvidenceSource | undefined;
    path: (string | number)[];
    readIds: string[];
  }> = [];
  const referenceIssues: ReportEvidenceIssue[] = [];
  const measuredSources = new Set(measurementFacts === undefined
    ? reportCitationManifest(payload, evidence, dataFreshness).measurementReads.map((read) => read.evidenceSource)
    : measurementFacts.map((fact) => fact.source));
  if (measuredSources.size > 0 && !report.observations.some((claim) => claim.evidenceOrigin === 'warehouse'
    && claim.evidenceSource !== undefined && measuredSources.has(claim.evidenceSource))) {
    referenceIssues.push({
      code: 'custom', path: ['observations'],
      message: 'Measured tool evidence is available. Include at least one warehouse observation describing an actual returned measurement with its exact source and matching read IDs. Empty or inference-only observations omit available evidence; historical gaps do not erase current measurements. Do not invent facts or IDs.',
    });
  }
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
      if (!call || !isMeasurementRead(call) || !auditSources(call).some((source) => toolSources.some((candidate) => candidate === source))) {
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
        ? `Warehouse citation ${source} is unsupported. Legacy sources require their exact assembled payload block. Every tool-surface citation requires a matching observed read in nonempty evidenceReadIds, including local evidence, so the actual dates and scope are displayed. Refused, failed, unavailable and skipped reads cannot support a warehouse citation.`
        : 'A warehouse claim must name the evidence source supported by an admissible executed read.',
    }];
  })];
}

/** Appended to a soil-citing statement that quotes a number without its basis; see soil/AGENTS.md §label-append. */
export const SOIL_MODEL_ESTIMATE_SENTENCE = ' Soil values are SoilGrids v2.0 250 m model estimates, not measurements.';
/** The short form for the fields too small for the sentence: risk headline and factors. */
export const SOIL_MODEL_ESTIMATE_SUFFIX = ' (SoilGrids model estimate)';
const MAX_STATEMENT_CHARACTERS = 500;
const MAX_RATIONALE_CHARACTERS = 900;
const MAX_HEADLINE_CHARACTERS = 300;
const MAX_FACTOR_CHARACTERS = 240;
/**
 * Soil property terms a SoilGrids number is quoted under. Bare "soil" is deliberately absent: soil
 * moisture and soil temperature are other sources, never SoilGrids estimates.
 */
const SOIL_PROPERTY_TERM = /\b(?:pH|soil (?:organic|carbon|nitrogen|texture)|organic carbon|SOC|clay|sand|silt|loam|loamy|texture|bulk density|cation exchange|CEC|coarse fragments|topsoil|SoilGrids)\b/i;

export interface SoilLabelOptions {
  /** Whether any SoilGrids value reached the model this turn (payload, brief soil section or the soil tool). */
  soilAvailable: boolean;
}

/** Append `suffix` within `limit` characters, truncating the text with an ellipsis when needed. */
function appendLabel(text: string, suffix: string, limit: number): string {
  const room = limit - suffix.length;
  const kept = text.length <= room ? text : `${text.slice(0, room - 1).trimEnd()}…`;
  return `${kept}${suffix}`;
}

/** A soil number without its basis: a digit, a soil property term (or a soilProperties citation), no "model estimate". */
function needsSoilLabel(text: string, citesSoil: boolean, soilAvailable: boolean): boolean {
  if (!/\d/.test(text) || /model estimate/i.test(text)) return false;
  return citesSoil || (soilAvailable && SOIL_PROPERTY_TERM.test(text));
}

/**
 * Deterministic, idempotent label rule (review M2). Whenever soil reached the model, ANY statement,
 * rationale, risk factor or headline that quotes a number beside a soil property term, and any
 * observation citing `soilProperties`, gets the canonical label unless it already says "model
 * estimate". It keys on text, not on `evidenceSource`, because the live report shape (measurement
 * facts on, model_inference statements, riskSummary sources forced to []) never carries a soil
 * citation. Never an error.
 */
export function labelSoilModelEstimates(report: RemediationReport, { soilAvailable }: SoilLabelOptions): RemediationReport {
  const observations = report.observations.map((observation) =>
    needsSoilLabel(observation.statement, observation.evidenceSource === 'soilProperties', soilAvailable)
      ? { ...observation, statement: appendLabel(observation.statement, SOIL_MODEL_ESTIMATE_SENTENCE, MAX_STATEMENT_CHARACTERS) }
      : observation);
  const remediation = report.remediation.map((item) =>
    needsSoilLabel(item.rationale, false, soilAvailable)
      ? { ...item, rationale: appendLabel(item.rationale, SOIL_MODEL_ESTIMATE_SENTENCE, MAX_RATIONALE_CHARACTERS) }
      : item);
  const { headline, factors } = report.riskSummary;
  const riskSummary = {
    ...report.riskSummary,
    headline: needsSoilLabel(headline, false, soilAvailable)
      ? appendLabel(headline, SOIL_MODEL_ESTIMATE_SUFFIX, MAX_HEADLINE_CHARACTERS) : headline,
    factors: factors.map((factor) => needsSoilLabel(factor, false, soilAvailable)
      ? appendLabel(factor, SOIL_MODEL_ESTIMATE_SUFFIX, MAX_FACTOR_CHARACTERS) : factor),
  };
  return { ...report, riskSummary, observations, remediation };
}

// --- Literature claim grounding: see `src/lib/server/services/AGENTS.md` §literature-grounding ---

/** One answered strategy-knowledge record: its server-written citation and the text its numbers come from. */
export interface LiteratureRecord {
  citation: LiteratureCitation;
  groundingText: string;
}

const MAX_RECORD_ID_CHARACTERS = 200;
const MAX_CITATIONS_PER_CLAIM = 8;
const MAX_GROUNDING_NOTE_CHARACTERS = 240;
const LITERATURE_RECORD_FIELDS = ['results', 'strategies', 'findings'] as const;
/** Finding fields whose numbers a claim may restate (plus its source title and year). */
const FINDING_GROUNDING_FIELDS = ['claim', 'magnitude', 'conditions', 'excerpt'] as const;
/** Strategy fields whose numbers a claim may restate (plus each citation's title, year and excerpt). */
const STRATEGY_GROUNDING_FIELDS = [
  'name', 'summary', 'actions', 'application_rate', 'timing', 'slope_guidance', 'soil_conditions',
  'scale', 'time_to_effect', 'benefits', 'risks_limitations', 'nrcs_practice_code',
] as const;

function plainObject(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function boundedText(value: unknown, maximum: number): string | undefined {
  if (typeof value !== 'string') return undefined;
  const text = value.trim();
  if (!text) return undefined;
  return text.length > maximum ? `${text.slice(0, maximum - 1)}…` : text;
}

function exactRecordId(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const id = value.trim();
  return id && id.length <= MAX_RECORD_ID_CHARACTERS ? id : undefined;
}

function httpsUrl(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  try {
    const url = new URL(value.trim());
    return url.protocol === 'https:' && url.href.length <= 2_048 ? url.href : undefined;
  } catch {
    return undefined;
  }
}

/** Every string and finite number inside a value, bounded in depth. */
function collectText(value: unknown, depth = 0): string[] {
  if (depth > 4) return [];
  if (typeof value === 'string') return [value];
  if (typeof value === 'number' && Number.isFinite(value)) return [String(value)];
  if (Array.isArray(value)) return value.flatMap((entry) => collectText(entry, depth + 1));
  const object = plainObject(value);
  return object ? Object.values(object).flatMap((entry) => collectText(entry, depth + 1)) : [];
}

/** Record arrays, raw or in the `{ entries, omittedEntries }` shape `boundedEvidence` produces. */
function recordEntries(value: unknown): Record<string, unknown>[] {
  const entries = Array.isArray(value) ? value : plainObject(value)?.entries;
  if (!Array.isArray(entries)) return [];
  // `.map` + a type-predicate `.filter` instead of `.flatMap(() => plainObject(entry) ?? [])`:
  // the latter's `Record<string, unknown> | never[]` return type resolves `flatMap`'s generic to
  // `unknown` on this TS/lib combination, back-widening the whole function's declared return type.
  return entries.map((entry) => plainObject(entry)).filter((entry): entry is Record<string, unknown> => entry !== null);
}

function findingRecord(entry: Record<string, unknown>): LiteratureRecord | null {
  const recordId = exactRecordId(entry.finding_id);
  if (!recordId) return null;
  const source: Record<string, unknown> = plainObject(entry.source) ?? {};
  const citation: LiteratureCitation = {
    recordId, kind: 'finding',
    title: boundedText(source.title, 300) ?? boundedText(entry.claim, 300) ?? recordId,
  };
  const magnitude = boundedText(entry.magnitude, 200);
  if (magnitude) citation.magnitude = magnitude;
  if ((LITERATURE_DIRECTIONS as readonly unknown[]).includes(entry.direction)) citation.direction = entry.direction as LiteratureDirection;
  const conditions = boundedText(entry.conditions, 600);
  if (conditions) citation.conditions = conditions;
  const sourceUrl = httpsUrl(source.url);
  if (sourceUrl) citation.sourceUrl = sourceUrl;
  return {
    citation,
    groundingText: collectText([...FINDING_GROUNDING_FIELDS.map((field) => entry[field]), source.title, source.year]).join('\n'),
  };
}

function strategyRecord(entry: Record<string, unknown>): LiteratureRecord | null {
  const recordId = exactRecordId(entry.strategy_id);
  if (!recordId) return null;
  const citations = recordEntries(entry.citations);
  const citation: LiteratureCitation = { recordId, kind: 'strategy', title: boundedText(entry.name, 300) ?? recordId };
  const sourceUrl = citations.map((card) => httpsUrl(card.url)).find(Boolean);
  if (sourceUrl) citation.sourceUrl = sourceUrl;
  return {
    citation,
    groundingText: collectText([
      ...STRATEGY_GROUNDING_FIELDS.map((field) => entry[field]),
      ...citations.map((card) => [card.title, card.year, card.excerpt]),
    ]).join('\n'),
  };
}

/**
 * The citable records in one answered strategy-knowledge payload: findings by `finding_id`, strategies
 * (search hits and full records) by `strategy_id`. A refusal or non-literature payload yields none.
 */
export function literatureRecordsFromResult(result: unknown): LiteratureRecord[] {
  const root = plainObject(result);
  if (!root || root.error || root.refusal_code) return [];
  return LITERATURE_RECORD_FIELDS.flatMap((field) => recordEntries(root[field]))
    .flatMap((entry) => findingRecord(entry) ?? strategyRecord(entry) ?? []);
}

/** One entry per record id: the first citation's fields win, later records fill gaps and add grounding text. */
function literatureRecordIndex(records: readonly LiteratureRecord[]): Map<string, LiteratureRecord> {
  const index = new Map<string, LiteratureRecord>();
  for (const record of records) {
    const existing = index.get(record.citation.recordId);
    index.set(record.citation.recordId, existing && existing.citation.kind === record.citation.kind ? {
      citation: { ...record.citation, ...existing.citation },
      groundingText: `${existing.groundingText}\n${record.groundingText}`,
    } : existing ?? record);
  }
  return index;
}

// Dash/fraction-slash code points, not literal glyphs (wave-2 fix-stage review): an editor or
// formatter silently normalising one look-alike dash into another would narrow this class without
// a visible diff, exactly the failure class this guards against. U+2010-U+2014 hyphens/dashes (the
// em dash included), U+2212 minus; U+2044 fraction slash maps to "/", the rest to "-".
const DASH_CODE_POINTS = [0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2212];
const FRACTION_SLASH = String.fromCharCode(0x2044);
const DASH_CLASS = new RegExp(`[${DASH_CODE_POINTS.map((codePoint) => String.fromCharCode(codePoint)).join('')}${FRACTION_SLASH}]`, 'g');

/** NFKC, then the dash/fraction-slash class above, and thousands commas dropped. */
function normalizeNumericText(text: string): string {
  // NFKC already folds U+FE58 (small em dash) to U+2014, so the class needs no compatibility forms.
  return text.normalize('NFKC')
    .replace(DASH_CLASS, (character) => character === FRACTION_SLASH ? '/' : '-')
    .replace(/(\d),(?=\d{3}(?!\d))/g, '$1');
}

/** A digit run, or a leading-dot decimal ("`.5`") with no digit before the point. */
const DECIMAL = String.raw`(?:\d+(?:\.\d+)?|\.\d+)`;
const PERCENT = String.raw`(?:\s*(?:%|percent\b))?`;
/** A number, range ("20-30%", "20 to 30 percent") or ratio ("1/3", "2:1") not glued to a word ("CO2", "F8101"). */
const NUMERIC_CLAIM = new RegExp(
  String.raw`(?<![\p{L}\p{N}.])-?(${DECIMAL})${PERCENT}(?:\s*(?:-|to)\s*-?(${DECIMAL})${PERCENT}|\s*[/:]\s*(${DECIMAL}))?`,
  'giu',
);

/** Each numeric claim in a text with its unsigned component values; the sign is carried by the cited direction, not here. */
function numericClaims(text: string): { text: string; values: string[] }[] {
  return [...normalizeNumericText(text).matchAll(NUMERIC_CLAIM)].map((match) => ({
    text: match[0],
    values: [match[1], match[2], match[3]].filter((value): value is string => value !== undefined)
      .map((value) => String(Math.abs(Number(value)))),
  }));
}

/**
 * Magnitude words `NUMERIC_CLAIM` cannot see (no digits at all): a word-form claim is grounded only
 * when the SAME lemma appears in the cited record's own text, never converted to an assumed percent
 * (wave-2 fix-stage review) -- "halves" and "doubles" don't carry a reliable numeric equivalent, and
 * a wrong one would be worse than declining to ground it.
 */
const MAGNITUDE_WORD_LEMMA: Record<string, string> = {
  half: 'half', halved: 'half', halves: 'half',
  double: 'double', doubled: 'double', doubles: 'double',
  triple: 'triple', tripled: 'triple', triples: 'triple',
  quadruple: 'quadruple', quadrupled: 'quadruple', quadruples: 'quadruple',
  thirds: 'thirds', quarters: 'quarters',
};
const MAGNITUDE_WORD = new RegExp(`\\b(${Object.keys(MAGNITUDE_WORD_LEMMA).join('|')})\\b`, 'gi');

/** Word-form magnitude lemmas present in a text (lower-cased, deduplicated by match order). */
function wordMagnitudeClaims(text: string): string[] {
  return [...text.matchAll(MAGNITUDE_WORD)].map((match) => MAGNITUDE_WORD_LEMMA[match[1].toLowerCase()]);
}

const NEGATED = /\b(?:not|never|cannot|can't|won't|wouldn't|shouldn't|isn't|aren't|unlikely|no guarantee|without)\b/;
const SITE_ANCHOR = /\b(?:your|here|locally|on[- ]site|this (?:site|field|farm|property|parcel|location|ranch|pasture|plot|land|area|point))\b/;
const MODAL = /\b(?:will|would|should|can|could|may|might|expect(?:s|ed)?|likely|project(?:s|ed)?|predict(?:s|ed)?|anticipate[sd]?)\b/;
const EFFECT = /\b(?:reduc\w*|cut\w*|lower\w*|rais\w*|increas\w*|boost\w*|improv\w*|sav\w*|gain\w*|yield\w*|drop\w*|declin\w*|ris(?:e|es|ing)|achiev\w*|expect\w*|see)\b/;
const YOU_PROMISE = /\byou(?:'ll| will| would| could| can| should| may| might)?\s+(?:likely\s+|probably\s+)?(?:expect|see|save|gain|get|achieve|cut|reduce|lower|raise|increase|boost|improve)\b/;

/**
 * A sentence that restates a cited number as an outcome to expect at the user's site.
 *
 * Negation is scoped to the CLAUSE that carries the promise, not the whole sentence: a trailing
 * hedge in a later clause ("...here, not counting runoff.") must not blanket-suppress a promise
 * made earlier in the same sentence.
 */
function promisesSiteOutcome(sentence: string): boolean {
  if (numericClaims(sentence).length === 0) return false;
  return sentence.toLowerCase().split(/[,;]/).some((clause) =>
    !NEGATED.test(clause) && (YOU_PROMISE.test(clause) || (SITE_ANCHOR.test(clause) && MODAL.test(clause) && EFFECT.test(clause))));
}

const INCREASE_VERBS = /\b(?:increas\w*|rais\w*|boost\w*|improv\w*|ris(?:e|es|ing)|grow\w*|gain\w*)\b/;
const DECREASE_VERBS = /\b(?:reduc\w*|cut\w*|lower\w*|decreas\w*|declin\w*|drop\w*|shrink\w*|diminish\w*)\b/;

/**
 * Whether a sentence's effect verb reverses a cited record's own increase/decrease direction.
 * Only strict increase/decrease directions are checked -- `mixed`/`conditional`/`no_effect`
 * records carry no single sign to contradict, matching S4's "matching is normalised, not signed".
 */
function contradictsDirection(sentence: string, direction: LiteratureDirection): boolean {
  if (direction !== 'increase' && direction !== 'decrease') return false;
  const lower = sentence.toLowerCase();
  const increases = INCREASE_VERBS.test(lower);
  const decreases = DECREASE_VERBS.test(lower);
  if (increases === decreases) return false; // neither or both verb families: not a clear reversal
  return direction === 'increase' ? decreases : increases;
}

/** Why a claim's text is not grounded in its cited records, or null when every number is. */
function ungroundedReason(text: string, cited: readonly LiteratureRecord[]): string | null {
  const claims = numericClaims(text);
  const wordClaims = wordMagnitudeClaims(text);
  if (claims.length === 0 && wordClaims.length === 0) return null;
  const reportedByRecord = cited.map((record) => ({
    direction: record.citation.direction,
    values: new Set(numericClaims(record.groundingText).flatMap((claim) => claim.values)),
    words: new Set(wordMagnitudeClaims(record.groundingText)),
  }));
  const unmatched = claims.find((claim) => claim.values.some((value) => !reportedByRecord.some((record) => record.values.has(value))));
  if (unmatched) return `It states ${unmatched.text.trim()}, which no cited record reports.`;
  const unmatchedWord = wordClaims.find((word) => !reportedByRecord.some((record) => record.words.has(word)));
  if (unmatchedWord) return `It states a magnitude ("${unmatchedWord}") that no cited record's own text uses.`;
  const sentences = text.split(/(?<=[.!?;])\s+|\n+/);
  // A sentence's direction is checked only against the records that report ITS numbers, so a claim
  // citing an increase finding and a decrease finding can restate each in its own sentence.
  const reversed = sentences.find((sentence) => {
    const values = numericClaims(sentence).flatMap((claim) => claim.values);
    const directions = reportedByRecord
      .filter((record) => values.some((value) => record.values.has(value)))
      .flatMap((record) => record.direction === 'increase' || record.direction === 'decrease' ? [record.direction] : []);
    return directions.length > 0 && directions.every((direction) => contradictsDirection(sentence, direction));
  });
  if (reversed) return `It states an effect direction that reverses a cited record's own reported direction.`;
  if (sentences.some(promisesSiteOutcome)) {
    return 'It restates a literature magnitude as an expected outcome at this site; cited studies describe other sites.';
  }
  return null;
}

function downgradeLiteratureClaim(value: Record<string, unknown>, reason: string): Record<string, unknown> {
  const downgraded: Record<string, unknown> = {
    ...value, evidenceOrigin: 'model_inference',
    groundingNote: boundedText(reason, MAX_GROUNDING_NOTE_CHARACTERS),
  };
  delete downgraded.evidenceSource;
  delete downgraded.literatureRecordIds;
  delete downgraded.literatureCitations;
  return downgraded;
}

/**
 * Bind each literature claim to the records it cites. Its `literatureRecordIds` resolve against this
 * turn's answered records and the server writes `literatureCitations` from them; a claim with no valid
 * id, a number no cited record reports, or a literature magnitude promised at this site is DOWNGRADED
 * to model_inference with a `groundingNote` -- never a validation issue. A no-op without records, so
 * the "no literature answered" rule in `resolveProviderMeasurementReport` still applies. Runs after
 * `pairLiteratureProvenance`.
 */
export function groundLiteratureClaims(
  input: Record<string, unknown> | null,
  records: readonly LiteratureRecord[],
): Record<string, unknown> | null {
  if (!input || records.length === 0) return input;
  const index = literatureRecordIndex(records);
  const ground = (claim: unknown, textFields: readonly string[]): unknown => {
    const value = plainObject(claim);
    // A conflicting (non-literature) source is left for the validator, exactly as pairing leaves it.
    if (!value || value.evidenceOrigin !== 'literature'
      || (value.evidenceSource !== undefined && value.evidenceSource !== STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE)) return claim;
    const requested = [...new Set((Array.isArray(value.literatureRecordIds) ? value.literatureRecordIds : [])
      .flatMap((id: unknown) => exactRecordId(id) ?? []))];
    const cited = requested.flatMap((id) => index.get(id) ?? []).slice(0, MAX_CITATIONS_PER_CLAIM);
    const text = textFields.flatMap((field) => typeof value[field] === 'string' ? [value[field] as string] : []).join('\n');
    const reason = cited.length > 0 ? ungroundedReason(text, cited)
      : requested.length > 0 ? `None of its cited records (${requested.slice(0, 3).join(', ')}) was returned by this analysis's literature lookups.`
        : 'It cites no literature record.';
    if (reason) return downgradeLiteratureClaim(value, reason);
    return {
      ...value,
      evidenceSource: STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE,
      literatureRecordIds: cited.map((record) => record.citation.recordId),
      literatureCitations: cited.map((record) => ({ ...record.citation })),
    };
  };
  return {
    ...input,
    observations: Array.isArray(input.observations) ? input.observations.map((claim) => ground(claim, ['statement'])) : input.observations,
    remediation: Array.isArray(input.remediation) ? input.remediation.map((claim) => ground(claim, ['title', 'rationale'])) : input.remediation,
  };
}
