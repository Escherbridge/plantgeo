import { z } from 'zod/v4';
import type { RegionalAnalysisEvidence } from '@/lib/regional-intelligence';

const calendarDay = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine((value) => {
  const time = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value;
});

/** Validates server evidence separately from the model-authored report. */
export const regionalAnalysisEvidenceSchema = z.object({
  version: z.literal(1),
  stages: z.array(z.object({
    id: z.string().min(1).max(100),
    label: z.string().min(1).max(200),
    status: z.enum(['completed', 'partial', 'unavailable']),
  }).strict()).max(20),
  toolCalls: z.array(z.object({
    id: z.string().min(1).max(100),
    stage: z.string().min(1).max(100),
    tool: z.string().min(1).max(100),
    source: z.string().min(1).max(100).optional(),
    sources: z.array(z.string().min(1).max(100)).min(2).max(8).optional(),
    selectedDate: calendarDay.optional(),
    validDates: z.array(calendarDay).max(128).optional(),
    observedDates: z.array(calendarDay).max(128).optional(),
    servedDates: z.array(calendarDay).max(128).optional(),
    location: z.object({ lat: z.number().min(-90).max(90), lon: z.number().min(-180).max(180) }).strict().optional(),
    status: z.enum(['observed', 'unavailable', 'refused', 'error', 'not_queried', 'governed_absence']),
    summary: z.string().max(2_000).optional(),
    reason: z.string().max(2_000).optional(),
  }).strict()).max(128),
  limitations: z.array(z.string().min(1).max(2_000)).max(40),
}).strict();

export function readRegionalAnalysisEvidence(value: unknown): RegionalAnalysisEvidence | null {
  const parsed = regionalAnalysisEvidenceSchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}
