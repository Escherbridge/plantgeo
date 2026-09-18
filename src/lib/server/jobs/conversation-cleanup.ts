// BullMQ job: delete AI conversations older than 30 days, daily at 3am UTC.
// Register in Next.js instrumentation hook (instrumentation.ts):
//   await import("@/lib/server/jobs/conversation-cleanup");

import { db } from "@/lib/server/db";
import { aiConversations } from "@/lib/server/db/schema";
import { lt, sql } from "drizzle-orm";
import { getRedisConnection } from "@/lib/server/redis";

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
  let bullmq: typeof import("bullmq") | undefined;

  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    bullmq = require("bullmq");
  } catch {
    // bullmq not installed — skip
  }

  if (bullmq) {
    // Canonical REDIS_URL parsing lives in `@/lib/server/redis`; see its
    // rationale pointer in `src/lib/server/AGENTS.md` §trpc / db / auth.
    const connection = getRedisConnection();

    const QUEUE_NAME = "conversation-cleanup";
    const queue = new bullmq.Queue(QUEUE_NAME, { connection });

    // Daily at 3am UTC — BullMQ cron pattern
    queue
      .upsertJobScheduler(
        "conversation-cleanup-daily",
        { pattern: "0 3 * * *" },
        { name: "cleanup" }
      )
      .catch(() => {
        // Non-fatal — Redis may not be available during build
      });

    new bullmq.Worker(
      QUEUE_NAME,
      async () => {
        return runConversationCleanup();
      },
      { connection }
    );
  }
}
