// BullMQ job: delete AI conversations older than 30 days, daily at 3am UTC.
// Register in Next.js instrumentation hook (instrumentation.ts):
//   await import("@/lib/server/jobs/conversation-cleanup");

import { db } from "@/lib/server/db";
import { aiConversations } from "@/lib/server/db/schema";
import { lt, sql } from "drizzle-orm";
import { registerBullmqCron } from "@/lib/server/jobs/shared";

/**
 * Deletes ai_conversations rows where updated_at < NOW() - 30 days.
 * CASCADE on ai_messages handles child row cleanup automatically.
 */
export async function runConversationCleanup(): Promise<{ deleted: number }> {
  const thirtyDaysAgo = sql`NOW() - INTERVAL '30 days'`;

  const deleted = await db
    .delete(aiConversations)
    .where(lt(aiConversations.updatedAt, thirtyDaysAgo));

  const count =
    typeof deleted === "object" && deleted !== null && "rowCount" in deleted
      ? (deleted as { rowCount: number }).rowCount
      : 0;

  console.log(`[conversation-cleanup] Deleted ${count} expired conversations`);
  return { deleted: count };
}

// ---------------------------------------------------------------------------
// BullMQ worker registration — server-side only
// ---------------------------------------------------------------------------

if (typeof window === "undefined") {
  // Daily at 3am UTC. See `src/lib/server/jobs/shared.ts` for the registration helper.
  registerBullmqCron({
    queueName: "conversation-cleanup",
    jobName: "cleanup",
    cronPattern: "0 3 * * *",
    handler: async () => runConversationCleanup(),
  });
}
