import { z } from 'zod/v4';
import { remediationReportSchema } from '@/lib/server/services/remediation-report';
import type { RegionalIntelligenceResponse } from '@/lib/regional-intelligence';

const savedReportSchema = remediationReportSchema.extend({
  aiGenerated: z.literal(true),
  webSources: z.array(z.object({ title: z.string(), url: z.url().refine(value => /^https?:\/\//i.test(value)) }).strict()),
  dataFreshness: z.record(z.string(), z.string()),
});

/** Validates historical report shape without repairing its evidence or dates. */
export function readSavedReport(role: string, value: unknown): RegionalIntelligenceResponse | null {
  if (role !== 'assistant') return null;
  const result = savedReportSchema.safeParse(value);
  return result.success ? result.data : null;
}
