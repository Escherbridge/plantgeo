import type { WaterGauge } from '@/lib/environmental/water';
import type { RemediationReport } from './remediation-report';

type GroundingIssue = { code: 'custom'; path: (string | number)[]; message: string };
const FLOW = '(?:stream[ -]?flow|river flow|discharge|flow)';
const CLASSIFICATION = '(?:critically low|below[ -]normal|above[ -]normal|low|high|normal|rising|declining|falling|increasing|decreasing|stable)';
const CLAIMS = new RegExp(`\\b(${CLASSIFICATION})\\s+${FLOW}\\b|\\b${FLOW}\\s+(?:is|was|remains|appears|is classified as|was classified as)\\s+(${CLASSIFICATION})\\b`, 'gi');

function supported(classification: string, gauge: WaterGauge | null): boolean {
  if (!gauge) return false;
  const claim = classification.toLowerCase().replace(/-/g, ' ');
  if (['rising', 'increasing', 'declining', 'falling', 'decreasing', 'stable'].includes(claim)) {
    const trend = ['increasing', 'rising'].includes(claim) ? 'rising'
      : ['declining', 'falling', 'decreasing'].includes(claim) ? 'declining' : 'stable';
    return gauge.trend === trend;
  }
  if (gauge.percentile === null || !Number.isFinite(gauge.percentile) || gauge.percentile < 0 || gauge.percentile > 100) return false;
  if (claim === 'low' || claim === 'below normal') return ['below_normal', 'low', 'critically_low'].includes(gauge.condition);
  if (claim === 'critically low') return gauge.condition === 'critically_low';
  if (claim === 'high' || claim === 'above normal') return gauge.condition === 'above_normal';
  return gauge.condition === 'normal';
}

/** Checks bounded flow phrases against the supplied nearest gauge; see services/AGENTS.md. */
export function reportFlowGroundingIssues(report: RemediationReport, gauge: WaterGauge | null): GroundingIssue[] {
  const fields: { text: string; path: (string | number)[] }[] = [
    { text: report.riskSummary.headline, path: ['riskSummary', 'headline'] },
    ...report.riskSummary.factors.map((text, index) => ({ text, path: ['riskSummary', 'factors', index] })),
    ...report.observations.map((item, index) => ({ text: item.statement, path: ['observations', index, 'statement'] })),
    ...report.remediation.flatMap((item, index) => [
      { text: item.title, path: ['remediation', index, 'title'] },
      { text: item.rationale, path: ['remediation', index, 'rationale'] },
    ]),
    { text: report.professionalConsultation, path: ['professionalConsultation'] },
  ];
  return fields.flatMap(({ text, path }) => {
    for (const match of text.matchAll(CLAIMS)) {
      const prefix = text.slice(0, match.index).split(/[.!?;\n]/).at(-1) ?? '';
      // Explicit denials of a classification are not affirmative evidence claims.
      if (/\b(?:not|no evidence of|(?:cannot|unable to) (?:establish|infer|classify|determine)(?:\s+(?:whether|if|that))?)\s*$/i.test(prefix)) continue;
      if (!supported(match[1] ?? match[2] ?? '', gauge)) return [{
        code: 'custom' as const, path,
        message: 'Unsupported streamflow classification or trend. The supplied nearest gauge does not support this comparison. Report its numeric discharge without classifying it; drought-based concerns may remain explicitly attributed to drought. Do not invent a comparator or trend.',
      }];
    }
    return [];
  });
}
