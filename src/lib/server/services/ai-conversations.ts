import { and, desc, eq, sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { aiConversations, aiMessages } from "@/lib/server/db/schema";
import type { ConversationTurn } from "@/lib/regional-intelligence";
import { conversationHistory, MAX_REPLAYED_TURNS } from './conversation-history';

const TITLE_MAX_LENGTH = 255;

export interface ConversationHandle {
  id: string;
  history: ConversationTurn[];
}

function deriveTitle(question: string | undefined, lat: number, lon: number): string {
  const trimmed = question?.trim();
  if (trimmed) {
    return trimmed.length > TITLE_MAX_LENGTH
      ? `${trimmed.slice(0, TITLE_MAX_LENGTH - 1)}…`
      : trimmed;
  }
  return `Analysis at ${lat.toFixed(2)}, ${lon.toFixed(2)}`;
}

/**
 * Resolves the conversation for a request. History is read from the database
 * rather than the request body so a client cannot forge prior assistant turns.
 */
export async function openConversation(options: {
  userId: string;
  conversationId?: string;
  lat: number;
  lon: number;
  geohash: string;
  question?: string;
}): Promise<ConversationHandle> {
  const { userId, conversationId, lat, lon, geohash, question } = options;

  if (conversationId) {
    const [existing] = await db
      .select({ id: aiConversations.id })
      .from(aiConversations)
      .where(
        and(
          eq(aiConversations.id, conversationId),
          eq(aiConversations.userId, userId)
        )
      )
      .limit(1);

    if (existing) {
      const rows = await db
        .select({ role: aiMessages.role, content: aiMessages.content, structuredResponse: aiMessages.structuredResponse, createdAt: aiMessages.createdAt })
        .from(aiMessages)
        .where(eq(aiMessages.conversationId, existing.id))
        .orderBy(desc(aiMessages.createdAt), desc(sql`case when ${aiMessages.role} = 'user' then 0 else 1 end`), desc(aiMessages.id))
        .limit(MAX_REPLAYED_TURNS + 1);

      const history = conversationHistory([...rows].reverse());

      return { id: existing.id, history };
    }
  }

  const [created] = await db
    .insert(aiConversations)
    .values({
      userId,
      geohash,
      lat,
      lon,
      title: deriveTitle(question, lat, lon),
    })
    .returning({ id: aiConversations.id });

  return { id: created.id, history: [] };
}

/** Appends one exchange and advances the conversation's activity clock. */
export async function recordExchange(options: {
  conversationId: string;
  question: string;
  answer: string;
  structuredResponse: unknown;
}): Promise<{ assistantMessageId: string }> {
  const { conversationId, question, answer, structuredResponse } = options;

  const inserted = await db.insert(aiMessages).values([
    { conversationId, role: "user", content: question },
    {
      conversationId,
      role: "assistant",
      content: answer,
      structuredResponse: structuredResponse as Record<string, unknown>,
    },
  ]).returning({ id: aiMessages.id, role: aiMessages.role });

  await db
    .update(aiConversations)
    .set({
      messageCount: sql`${aiConversations.messageCount} + 2`,
      updatedAt: new Date(),
    })
    .where(eq(aiConversations.id, conversationId));
  const assistant = inserted.find(message => message.role === 'assistant');
  if (!assistant) throw new Error('The saved exchange did not return an assistant message');
  return { assistantMessageId: assistant.id };
}
