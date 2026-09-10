import { z } from 'zod/v4';
import {
  EVIDENCE_ORIGINS,
  INTERVENTION_STRATEGIES,
  PROFESSIONAL_DISCIPLINES,
  REGIONAL_EVIDENCE_SOURCES,
} from '@/lib/regional-intelligence';

/** Canonical report contract shared by provider tools, the agent loop and the route. */
export const remediationReportSchema = z
  .object({
    riskSummary: z
      .object({
        level: z.enum(['low', 'moderate', 'high', 'critical']),
        headline: z.string().trim().min(1).max(300),
        factors: z.array(z.string().trim().min(1).max(240)).max(8),
        evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
        evidenceSources: z.array(z.enum(REGIONAL_EVIDENCE_SOURCES)).max(9),
      })
      .strict(),
    observations: z
      .array(
        z
          .object({
            statement: z.string().trim().min(1).max(500),
            evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
            evidenceSource: z.enum(REGIONAL_EVIDENCE_SOURCES).optional(),
          })
          .strict()
      )
      .max(12),
    remediation: z
      .array(
        z
          .object({
            strategy: z.enum(INTERVENTION_STRATEGIES),
            title: z.string().trim().min(1).max(160),
            rationale: z.string().trim().min(1).max(900),
            timeframe: z.enum(['immediate', 'short_term', 'long_term']),
            confidence: z.enum(['low', 'moderate', 'high']),
            consultProfessionals: z
              .array(z.enum(PROFESSIONAL_DISCIPLINES))
              .max(5),
            evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
            evidenceSource: z.enum(REGIONAL_EVIDENCE_SOURCES).optional(),
          })
          .strict()
      )
      .max(8),
    professionalConsultation: z.string().trim().min(1).max(600),
  })
  .strict();

/** Model-visible constraints generated from the exact runtime validator. */
export const REMEDIATION_REPORT_JSON_SCHEMA = z.toJSONSchema(remediationReportSchema, {
  target: 'draft-7',
});

export type RemediationReport = z.infer<typeof remediationReportSchema>;
