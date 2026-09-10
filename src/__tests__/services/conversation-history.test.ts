import { describe, expect, it } from 'vitest';
import { conversationHistory } from '@/lib/server/services/conversation-history';

const report = {
  aiGenerated: true,
  riskSummary: { level: 'low', headline: 'Saved assessment', factors: [], evidenceOrigin: 'model_inference', evidenceSources: [] },
  observations: [{ statement: 'Drought release dated September 1.', evidenceOrigin: 'warehouse', evidenceSource: 'drought' }],
  remediation: [], professionalConsultation: 'Consult an ecologist.',
  webSources: [{ title: 'Reference', url: 'https://example.org/source' }], dataFreshness: { drought: '2026-09-01' },
};
const row = { role: 'assistant', content: 'Headline only', createdAt: '2026-09-10T12:00:00Z', structuredResponse: report };

describe('bounded historical model context', () => {
  it('retains actual validated report observations, citations and original source dates as historical evidence', () => {
    const history = conversationHistory([row]);
    expect(history[0].content).toContain('Historical saved AI answer (2026-09-10T12:00:00.000Z)');
    expect(history[0].content).toContain('not current or independently verified evidence');
    const serializedReport = history[0].content.split('Saved structured report with original citations and source dates:\n')[1];
    expect(JSON.parse(serializedReport)).toEqual(report);
  });
  it('keeps legacy text but never reconstructs invalid structured evidence', () => {
    const history = conversationHistory([{ ...row, structuredResponse: { observations: 'invented legacy shape' } }]);
    expect(history[0].content).toContain('Headline only');
    expect(history[0].content).not.toContain('invented legacy shape');
  });
  it('bounds total replay and retains the newest user turn with explicit omission notices', () => {
    const rows = Array.from({ length: 20 }, (_, index) => ({ ...row, role: index % 2 ? 'assistant' : 'user', content: `${index}: ${'x'.repeat(30_000)}`, structuredResponse: null }));
    rows.push({ ...row, role: 'user', content: 'Newest question', structuredResponse: null });
    const history = conversationHistory(rows);
    expect(history.length).toBeLessThanOrEqual(8);
    expect(history.reduce((total, turn) => total + turn.content.length, 0)).toBeLessThanOrEqual(64_000);
    expect(history.at(-1)?.content).toBe('Newest question');
    expect(history.some(turn => turn.content.includes('omitted from model context'))).toBe(true);
  });
  it('omits an oversized structured report whole rather than silently clipping evidence', () => {
    const huge = { ...report, webSources: [{ title: 'x'.repeat(40_000), url: 'https://example.org/source' }] };
    const [history] = conversationHistory([{ ...row, structuredResponse: huge }]);
    expect(history.content).toContain('omitted from model context');
    expect(history.content).not.toContain('Saved structured report with original citations');
    expect(history.content).toContain('Headline only');
  });
});
