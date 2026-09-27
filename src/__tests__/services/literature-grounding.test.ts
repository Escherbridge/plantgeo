import { describe, expect, it } from 'vitest';
import {
  REMEDIATION_REPORT_JSON_SCHEMA,
  groundLiteratureClaims,
  literatureRecordsFromResult,
  normalizeProviderReport,
  pairLiteratureProvenance,
  remediationReportSchema,
  reportSchemaForCitations,
  resolveProviderMeasurementReport,
} from '@/lib/server/services/remediation-report';
import type { RegionalAnalysisEvidence } from '@/lib/regional-intelligence';

/** The reduced-tillage finding the 2026-09-26 evals turned on: a U+2212 magnitude with a MIXED direction. */
const tillageFinding = {
  rank: 1,
  finding_id: 'sk-finding-reduced-tillage-F8101',
  claim: 'Reduced tillage lowered soil evaporation (relative median change = −65%) while soluble phosphorus losses rose.',
  conditions: 'Northern Great Plains irrigated cropland; multi-year field trials.',
  direction: 'mixed',
  magnitude: '−65%',
  study_type: 'field_trial',
  evidence_strength: 'peer_reviewed_study',
  linked_strategy_ids: ['reduced-tillage-no-till'],
  excerpt: 'Soil evaporation accounted for about 1/3 of irrigated water loss; runoff fell 20–30% under residue.',
  review_state: 'unreviewed',
  source: { title: 'Tillage and water loss in the Northern Great Plains', url: 'https://example.org/tillage-study', publisher: 'Example Press', year: 2019 },
};

/** Shaped like agri-data-service `strategy_knowledge.ask` for `search_strategy_research_findings`. */
const findingsPayload = {
  tool: 'search_strategy_research_findings', evidence_domain: 'literature_reference',
  cite_as: { evidenceOrigin: 'literature', evidenceSource: 'strategy-knowledge' },
  corpus_version: 'abc123', results: [tillageFinding], result_count: 1, note: 'Literature, NOT a measurement.',
};

/** Shaped like `get_environmental_strategies`: full records with citation cards. */
const strategyPayload = {
  tool: 'get_environmental_strategies', evidence_domain: 'literature_reference',
  strategies: [{
    strategy_id: 'reduced-tillage-no-till', name: 'Reduced tillage / no-till',
    summary: 'Leave crop residue on the surface and minimise soil disturbance.',
    application_rate: 'Keep at least 40% residue cover after planting.',
    citations: [
      { title: 'Plain-http guide', url: 'http://example.org/insecure' },
      { title: 'NRCS practice standard 329', url: 'https://example.org/nrcs-329', year: 2020 },
    ],
  }],
  not_found: [], result_count: 1,
};

const records = [...literatureRecordsFromResult(findingsPayload), ...literatureRecordsFromResult(strategyPayload)];

function recommendation(rationale: string, literatureRecordIds?: string[]) {
  return {
    strategy: 'cover_cropping', title: 'Screen reduced tillage for water savings', rationale,
    timeframe: 'long_term', confidence: 'low', consultProfessionals: ['agronomist'],
    evidenceOrigin: 'literature', evidenceSource: 'strategy-knowledge',
    ...(literatureRecordIds ? { literatureRecordIds } : {}),
  };
}

function report(...remediation: Record<string, unknown>[]) {
  return {
    riskSummary: { level: 'low', headline: 'Interpretation.', factors: [], evidenceOrigin: 'model_inference', evidenceSources: [] },
    observations: [], remediation, professionalConsultation: 'Consult an agronomist.',
  };
}

function groundedRemediation(...remediation: Record<string, unknown>[]) {
  const grounded = groundLiteratureClaims(report(...remediation), records);
  return (grounded?.remediation ?? []) as Record<string, unknown>[];
}

describe('literature records from answered strategy-knowledge payloads', () => {
  it('reads finding and strategy records with their server-owned citation fields, https links only', () => {
    expect(records.map((record) => record.citation)).toEqual([
      {
        recordId: 'sk-finding-reduced-tillage-F8101', kind: 'finding',
        title: 'Tillage and water loss in the Northern Great Plains',
        magnitude: '−65%', direction: 'mixed',
        conditions: 'Northern Great Plains irrigated cropland; multi-year field trials.',
        sourceUrl: 'https://example.org/tillage-study',
      },
      { recordId: 'reduced-tillage-no-till', kind: 'strategy', title: 'Reduced tillage / no-till', sourceUrl: 'https://example.org/nrcs-329' },
    ]);
  });

  it('reads nothing from a refusal, and search hits without a source keep the claim as their title', () => {
    expect(literatureRecordsFromResult({ tool: 'search_environmental_strategies', error: 'strategy_knowledge_unavailable', evidence_domain: 'literature_reference' })).toEqual([]);
    expect(literatureRecordsFromResult(null)).toEqual([]);
    const [hit] = literatureRecordsFromResult({ results: [{ finding_id: 'finding-0', claim: 'Reported effect 0.', direction: 'sideways', source: { url: 'javascript:alert(1)' } }] });
    expect(hit.citation).toEqual({ recordId: 'finding-0', kind: 'finding', title: 'Reported effect 0.' });
  });
});

describe('groundLiteratureClaims', () => {
  it('keeps the F8101 "−65%" claim as literature and writes its mixed direction and conditions from the record', () => {
    for (const rationale of [
      'Northern Great Plains trials reported a −65% relative median change in soil evaporation under reduced tillage, with a mixed overall effect.',
      'Cited trials report roughly -65 % less soil evaporation under reduced tillage, while phosphorus losses rose.',
      'Cited trials report ~65% lower soil evaporation under reduced tillage.',
    ]) {
      const [claim] = groundedRemediation(recommendation(rationale, ['sk-finding-reduced-tillage-F8101']));
      expect(claim).toMatchObject({
        evidenceOrigin: 'literature', evidenceSource: 'strategy-knowledge',
        literatureRecordIds: ['sk-finding-reduced-tillage-F8101'],
        literatureCitations: [expect.objectContaining({ magnitude: '−65%', direction: 'mixed', conditions: expect.stringContaining('Northern Great Plains') })],
      });
      expect(claim).not.toHaveProperty('groundingNote');
    }
  });

  it('matches a range against the record whether it is written with an en-dash or in words', () => {
    for (const rationale of ['Residue cut runoff by 20–30% in the cited trials.', 'Residue cut runoff by 20 to 30 percent in the cited trials.', 'About 1/3 of irrigated water loss was soil evaporation in the cited trials.']) {
      expect(groundedRemediation(recommendation(rationale, ['sk-finding-reduced-tillage-F8101']))[0]).toHaveProperty('evidenceOrigin', 'literature');
    }
    // One endpoint the record never reports fails the whole range.
    const [widened] = groundedRemediation(recommendation('Residue cut runoff by 20–35% in the cited trials.', ['sk-finding-reduced-tillage-F8101']));
    expect(widened).toMatchObject({ evidenceOrigin: 'model_inference', groundingNote: expect.stringContaining('20-35%') });
  });

  it('grounds a leading-dot decimal and a word-form magnitude only against a record using the same word', () => {
    // ".65" has no digit before the point; NUMERIC_CLAIM must still see it as a number, matched
    // against the SAME value the record reports -- not merely because F8101 also happens to say "65".
    const dotDecimalFinding = literatureRecordsFromResult({
      results: [{ finding_id: 'sk-finding-dot-decimal', claim: 'The index moved by .65 in the cited trials.', direction: 'increase' }],
    });
    expect(groundLiteratureClaims(report(recommendation('Cited trials report a change of about .65 in the evaporation index.', ['sk-finding-dot-decimal'])), dotDecimalFinding)?.remediation)
      .toMatchObject([{ evidenceOrigin: 'literature' }]);
    // A DIFFERENT leading-dot value the record never reports still fails, exactly like any other number.
    expect(groundLiteratureClaims(report(recommendation('Cited trials report a change of about .80 in the evaporation index.', ['sk-finding-dot-decimal'])), dotDecimalFinding)?.remediation)
      .toMatchObject([{ evidenceOrigin: 'model_inference' }]);
    // "halves" carries no digits at all: F8101's own text never uses "half"/"halved"/"halves", so a
    // word-form magnitude claim citing it is downgraded rather than left unchecked.
    const [halved] = groundedRemediation(recommendation('Reduced tillage roughly halves evaporation losses in the cited trials.', ['sk-finding-reduced-tillage-F8101']));
    expect(halved).toMatchObject({ evidenceOrigin: 'model_inference', groundingNote: expect.stringContaining('"half"') });
    // A record whose OWN text uses the same lemma grounds the claim: "halved" and "halves" both map
    // to the lemma "half", so the claim need not repeat the record's exact inflection.
    const halvingFinding = literatureRecordsFromResult({
      results: [{ finding_id: 'sk-finding-halving', claim: 'Cover crops halved nitrogen runoff in the cited trials.', direction: 'decrease' }],
    });
    expect(groundLiteratureClaims(report(recommendation('Cover crops halves nitrogen runoff.', ['sk-finding-halving'])), halvingFinding)?.remediation)
      .toMatchObject([{ evidenceOrigin: 'literature' }]);
  });

  it('downgrades a claim with a number no cited record reports, fixing its pairing', () => {
    const [claim] = groundedRemediation(recommendation('Cited trials report 80% less soil evaporation under reduced tillage.', ['sk-finding-reduced-tillage-F8101']));
    expect(claim).toMatchObject({ evidenceOrigin: 'model_inference', groundingNote: 'It states 80%, which no cited record reports.' });
    for (const field of ['evidenceSource', 'literatureRecordIds', 'literatureCitations']) expect(claim).not.toHaveProperty(field);
    // A number is bound to the record it cites, never to "anywhere in this turn's tool text":
    // the strategy record's 40% grounds a claim citing that strategy, not one citing only the finding.
    expect(groundedRemediation(recommendation('Keep at least 40% residue cover.', ['sk-finding-reduced-tillage-F8101']))[0])
      .toHaveProperty('evidenceOrigin', 'model_inference');
    expect(groundedRemediation(recommendation('Keep at least 40% residue cover.', ['reduced-tillage-no-till']))[0])
      .toHaveProperty('evidenceOrigin', 'literature');
    expect(groundedRemediation(recommendation('Keep at least 45% residue cover.', ['reduced-tillage-no-till']))[0])
      .toHaveProperty('evidenceOrigin', 'model_inference');
    // Words glued to digits are identifiers, not numbers.
    expect(groundedRemediation(recommendation('Reduced tillage may store CO2 in soil.', ['reduced-tillage-no-till']))[0])
      .toHaveProperty('evidenceOrigin', 'literature');
  });

  it('downgrades an unknown or missing id and drops unknown ids beside a valid one', () => {
    const [unknown, missing, mixed] = groundedRemediation(
      recommendation('Cited guidance favours residue retention.', ['sk-finding-invented-1']),
      recommendation('Cited guidance favours residue retention.'),
      recommendation('Cited guidance favours residue retention.', ['sk-finding-invented-1', 'reduced-tillage-no-till']),
    );
    expect(unknown).toMatchObject({ evidenceOrigin: 'model_inference', groundingNote: expect.stringContaining('sk-finding-invented-1') });
    expect(missing).toMatchObject({ evidenceOrigin: 'model_inference', groundingNote: 'It cites no literature record.' });
    expect(mixed).toMatchObject({
      evidenceOrigin: 'literature', literatureRecordIds: ['reduced-tillage-no-till'],
      literatureCitations: [{ recordId: 'reduced-tillage-no-till', kind: 'strategy', title: 'Reduced tillage / no-till', sourceUrl: 'https://example.org/nrcs-329' }],
    });
  });

  it('downgrades a literature magnitude restated as an outcome expected at this site, but not a hedge', () => {
    for (const rationale of [
      'Switching to no-till here would cut your soil evaporation by 65%.',
      'You can expect a 65% reduction in soil evaporation.',
      // A trailing hedge in a LATER clause of the same sentence must not blanket-suppress the
      // promise made earlier in it; negation is scoped per clause, not per sentence.
      'You can expect 65% less evaporation here, not counting runoff.',
    ]) {
      expect(groundedRemediation(recommendation(rationale, ['sk-finding-reduced-tillage-F8101']))[0])
        .toMatchObject({ evidenceOrigin: 'model_inference', groundingNote: expect.stringContaining('expected outcome at this site') });
    }
    expect(groundedRemediation(recommendation('The reported 65% reduction should not be expected at this site without local trials.', ['sk-finding-reduced-tillage-F8101']))[0])
      .toHaveProperty('evidenceOrigin', 'literature');
  });

  it('downgrades a claim whose effect verb reverses a cited record\'s own increase/decrease direction', () => {
    // F8101 is `direction: "mixed"`, which carries no single sign to contradict, so a strictly
    // directional record is needed to exercise the sign-flip guard.
    const evaporationDecrease = [...literatureRecordsFromResult({
      results: [{ finding_id: 'sk-finding-evaporation-decrease', claim: 'Reduced tillage decreased soil evaporation by 65%.', direction: 'decrease', magnitude: '65%' }],
    })];
    const grounded = (rationale: string) => (groundLiteratureClaims(report(recommendation(rationale, ['sk-finding-evaporation-decrease'])), evaporationDecrease)?.remediation as unknown[] | undefined ?? [])[0] as Record<string, unknown>;
    // "No-till" avoids the practice-name confound of "reduced tillage", whose own "reduced" would
    // otherwise trip the DECREASE_VERBS check regardless of the sentence's actual effect verb.
    expect(grounded('No-till farming increased soil evaporation by 65% in these trials.')).toMatchObject({
      evidenceOrigin: 'model_inference', groundingNote: expect.stringContaining('reverses'),
    });
    expect(grounded('No-till farming decreased soil evaporation by 65% in these trials.')).toHaveProperty('evidenceOrigin', 'literature');
    // A `mixed` direction record is never sign-checked.
    expect(groundedRemediation(recommendation('No-till farming increased soil evaporation by 65% in these trials.', ['sk-finding-reduced-tillage-F8101']))[0])
      .toHaveProperty('evidenceOrigin', 'literature');
  });

  it('checks each sentence\'s direction only against the cited records that report its numbers', () => {
    const opposingFindings = literatureRecordsFromResult({ results: [
      { finding_id: 'sk-finding-infiltration-increase', claim: 'Cover crops increased infiltration by 30%.', direction: 'increase', magnitude: '30%' },
      { finding_id: 'sk-finding-runoff-decrease', claim: 'Cover crops decreased runoff by 20%.', direction: 'decrease', magnitude: '20%' },
    ] });
    const grounded = (rationale: string) => (groundLiteratureClaims(
      report(recommendation(rationale, ['sk-finding-infiltration-increase', 'sk-finding-runoff-decrease'])), opposingFindings,
    )?.remediation as unknown[] | undefined ?? [])[0] as Record<string, unknown>;
    // Each sentence agrees with the record that reports its own number.
    expect(grounded('Cover crops increased infiltration by 30% in the cited trials. They also reduced runoff by 20%.'))
      .toHaveProperty('evidenceOrigin', 'literature');
    // Swapping the numbers reverses both records.
    expect(grounded('Cover crops increased runoff by 20% in the cited trials.'))
      .toMatchObject({ evidenceOrigin: 'model_inference', groundingNote: expect.stringContaining('reverses') });
  });

  it('folds an em dash range and grounds a strategy\'s NRCS practice code', () => {
    expect(groundedRemediation(recommendation('Residue cut runoff by 20—30% in the cited trials.', ['sk-finding-reduced-tillage-F8101']))[0])
      .toHaveProperty('evidenceOrigin', 'literature');
    const practice = literatureRecordsFromResult({ strategies: [{ strategy_id: 'residue-management', name: 'Residue management', nrcs_practice_code: '329' }] });
    const [claim] = (groundLiteratureClaims(report(recommendation('Follow NRCS practice standard 329 for residue management.', ['residue-management'])), practice)?.remediation ?? []) as Record<string, unknown>[];
    expect(claim).toHaveProperty('evidenceOrigin', 'literature');
  });

  it('never touches non-literature claims, conflicting sources or a turn without records', () => {
    const inference = { ...recommendation('A 90% saving is plausible.'), evidenceOrigin: 'model_inference' };
    delete (inference as Record<string, unknown>).evidenceSource;
    const conflicting = { ...recommendation('A 90% saving.', ['reduced-tillage-no-till']), evidenceSource: 'vegetation' };
    const [untouchedInference, untouchedConflict] = groundedRemediation(inference, conflicting);
    expect(untouchedInference).toEqual(inference);
    expect(untouchedConflict).toEqual(conflicting);
    const input = report(recommendation('A 90% saving.'));
    expect(groundLiteratureClaims(input, [])).toBe(input);
    expect(groundLiteratureClaims(null, records)).toBeNull();
  });

  it('grounds observations by their statement and yields a report the canonical validator accepts', () => {
    const input = {
      ...report(recommendation('Northern Great Plains trials reported a −65% relative median change in soil evaporation.', ['sk-finding-reduced-tillage-F8101'])),
      observations: [
        { statement: 'Cited trials report 80% lower evaporation.', evidenceOrigin: 'literature', literatureRecordIds: ['sk-finding-reduced-tillage-F8101'] },
        // Model-written citations and notes are server-owned and stripped before grounding.
        { statement: 'Cited guidance favours residue retention.', evidenceOrigin: 'literature', literatureRecordIds: ['reduced-tillage-no-till'], literatureCitations: [{ recordId: 'x', kind: 'finding', title: 'Invented', magnitude: '99%' }], groundingNote: 'fake' },
      ],
    };
    const answered: RegionalAnalysisEvidence = { version: 1, stages: [], limitations: [], toolCalls: [
      { id: 'additional-1', stage: 'additional', tool: 'search_strategy_research_findings', source: 'strategy-knowledge', status: 'answered' },
    ] };
    const grounded = groundLiteratureClaims(pairLiteratureProvenance(input), records);
    expect(grounded).toMatchObject({ observations: [
      { evidenceOrigin: 'model_inference', groundingNote: 'It states 80%, which no cited record reports.' },
      { evidenceOrigin: 'literature', literatureCitations: [expect.objectContaining({ recordId: 'reduced-tillage-no-till' })] },
    ] });
    expect(grounded).not.toHaveProperty('observations.1.groundingNote');
    const resolved = resolveProviderMeasurementReport(grounded, [], answered);
    expect(resolved.issues).toEqual([]);
    expect(remediationReportSchema.safeParse(normalizeProviderReport(resolved.report)).success).toBe(true);
  });
});

describe('literature fields in the report contracts', () => {
  it('never offers server-written citation fields to the model, and offers record ids only once literature answered', () => {
    expect(JSON.stringify(REMEDIATION_REPORT_JSON_SCHEMA)).not.toContain('literatureCitations');
    expect(JSON.stringify(REMEDIATION_REPORT_JSON_SCHEMA)).not.toContain('groundingNote');
    const manifest = { payloadSources: [], measurementReads: [] };
    expect(reportSchemaForCitations(manifest)).not.toHaveProperty('properties.remediation.items.properties.literatureRecordIds');
    const answered = reportSchemaForCitations(manifest, undefined, { literatureAnswered: true, literatureRecordIds: records.map((record) => record.citation.recordId) });
    expect(answered).toHaveProperty('properties.remediation.items.properties.literatureRecordIds.items.enum', ['sk-finding-reduced-tillage-F8101', 'reduced-tillage-no-till']);
    expect(answered).toHaveProperty('properties.observations.items.properties.literatureRecordIds.description', expect.stringContaining('finding_id'));
  });

  it('keeps literature fields paired with their origin and citation links https-only', () => {
    const literature = recommendation('Cited guidance favours residue retention.', ['reduced-tillage-no-till']);
    const citation = { recordId: 'reduced-tillage-no-till', kind: 'strategy', title: 'Reduced tillage / no-till' };
    expect(remediationReportSchema.safeParse(report({ ...literature, literatureCitations: [citation] })).success).toBe(true);
    expect(remediationReportSchema.safeParse(report({ ...literature, literatureCitations: [{ ...citation, sourceUrl: 'http://example.org' }] })).success).toBe(false);
    expect(remediationReportSchema.safeParse(report({ ...literature, evidenceOrigin: 'web', evidenceSource: undefined })).success).toBe(false);
    expect(remediationReportSchema.safeParse(report({ ...literature, groundingNote: 'Not grounded.' })).success).toBe(false);
    const { evidenceSource: _source, literatureRecordIds: _ids, ...inference } = literature;
    expect(remediationReportSchema.safeParse(report({ ...inference, evidenceOrigin: 'model_inference', groundingNote: 'It cites no literature record.' })).success).toBe(true);
  });
});
