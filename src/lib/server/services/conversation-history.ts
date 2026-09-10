import { readSavedReport } from '@/lib/regional-intelligence-saved-report';
import type { ConversationTurn } from '@/lib/regional-intelligence';

export const MAX_REPLAYED_TURNS = 8;
const MAX_TURN_CHARACTERS = 24_000;
const MAX_HISTORY_CHARACTERS = 64_000;
const OMITTED = '\n[Additional saved content omitted from model context to stay within the replay budget. The full record remains in chat history.]';

/** Replays bounded owner-loaded history, preserving complete validated historical reports. */
export function conversationHistory(rows: readonly { role: string; content: string; structuredResponse: unknown; createdAt: Date | string }[]): ConversationTurn[] {
  let remaining = MAX_HISTORY_CHARACTERS - OMITTED.length - 1;
  const result: ConversationTurn[] = [];
  const eligible = rows.filter(row => row.role === 'user' || row.role === 'assistant');
  const selected = eligible.slice(-MAX_REPLAYED_TURNS);
  for (const row of [...selected].reverse()) {
    const budget = Math.min(MAX_TURN_CHARACTERS - OMITTED.length - 1, remaining);
    if (budget <= OMITTED.length) break;
    let content = row.content;
    if (row.role === 'assistant') {
      const report = readSavedReport(row.role, row.structuredResponse);
      const timestamp = new Date(row.createdAt);
      const recorded = Number.isNaN(timestamp.getTime()) ? 'time unavailable' : timestamp.toISOString();
      const header = `Historical saved AI answer (${recorded}); as recorded, not current or independently verified evidence.\n`;
      if (report) {
        const structured = `\nSaved structured report with original citations and source dates:\n${JSON.stringify(report)}`;
        // Keep the report whole: omitting narration is safer than cutting structured evidence.
        content = header.length + structured.length <= budget
          ? header + structured
          : header + row.content.slice(0, Math.max(0, budget - header.length - OMITTED.length)) + OMITTED;
      } else {
        content = header + row.content;
      }
    }
    if (content.length > budget) content = content.slice(0, budget - OMITTED.length) + OMITTED;
    result.unshift({ role: row.role as 'user' | 'assistant', content });
    remaining -= content.length;
  }
  if ((result.length < selected.length || eligible.length > selected.length) && result.length) {
    result[0] = { ...result[0], content: OMITTED + '\n' + result[0].content };
  }
  return result;
}
