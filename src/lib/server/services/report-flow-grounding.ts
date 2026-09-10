import type { WaterGauge } from '@/lib/environmental/water';
import type { RemediationReport } from './remediation-report';

type GroundingIssue = { code: 'custom'; path: (string | number)[]; message: string };
const FLOW = '(?:stream[ -]?flow|river flow|discharge|flow)';
const CLASSIFICATION = '(?:critically low|below[ -]normal|above[ -]normal|low|high|normal|rising|declining|falling|increasing|decreasing|stable)';
const CLAIMS = new RegExp(`\\b(${CLASSIFICATION})\\s+${FLOW}\\b|\\b${FLOW}\\s+(?:is|was|remains|appears|is classified as|was classified as)\\s+(${CLASSIFICATION})\\b`, 'gi');
const CLAUSE_BOUNDARY = /[.!?;,\n]|\b(?:but|however|yet|nevertheless)\b/i;
const EVIDENCE_VERB = '(?:establish|infer|classify(?: (?:this )?as)?|determine|confirm|demonstrate|indicate)';
const DENIAL_BEFORE = new RegExp(`\\b(?:not|no evidence of|(?:cannot|can't|does not|doesn't|unable to) (?:be used to )?${EVIDENCE_VERB}(?:\\s+(?:whether|if|that))?|(?:insufficient|not enough|no) evidence to ${EVIDENCE_VERB}(?:\\s+(?:whether|if|that))?)\\s*$`, 'i');
const DENIAL_AFTER = /^\s+(?:(?:cannot|can't) be (?:inferred|established|determined|confirmed|demonstrated)|(?:is|was) not (?:established|confirmed|demonstrated))(?:\s+(?:from|by) (?:this|the|a) (?:single )?(?:reading|gauge value|measurement)(?: alone)?)?\s*$/i;

function explicitlyUnestablished(text: string, index: number, length: number): boolean {
  const before = text.slice(0, index).split(CLAUSE_BOUNDARY).at(-1) ?? '';
  const after = text.slice(index + length).split(CLAUSE_BOUNDARY)[0] ?? '';
  return DENIAL_BEFORE.test(before) || DENIAL_AFTER.test(after);
}

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
      // Explicit denials of a classification are not affirmative evidence claims.
      if (explicitlyUnestablished(text, match.index, match[0].length)) continue;
      if (!supported(match[1] ?? match[2] ?? '', gauge)) return [{
        code: 'custom' as const, path,
        message: 'Unsupported streamflow classification or trend. The supplied nearest gauge does not support this comparison. Report its numeric discharge without classifying it; drought-based concerns may remain explicitly attributed to drought. Do not invent a comparator or trend.',
      }];
    }
    return [];
  });
}
