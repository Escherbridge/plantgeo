// BullMQ job: send daily email digest to users with unread alerts.
// Cron: 8am UTC daily.
// Register in Next.js instrumentation hook (instrumentation.ts):
//   import "@/lib/server/jobs/email-digest";

import { db } from "@/lib/server/db";
import { environmentalAlerts, users } from "@/lib/server/db/schema";
import { eq, and, sql } from "drizzle-orm";
import { sendDigestEmail } from "@/lib/server/services/email";

/**
 * Aggregate unread alerts per user and send digest emails.
 */
export async function runEmailDigestJob(): Promise<{ sent: number }> {
  // Fetch all unread alerts with user email
  const rows = await db
    .select({
      userId: environmentalAlerts.userId,
      userEmail: users.email,
      alertId: environmentalAlerts.id,
      alertType: environmentalAlerts.alertType,
      severity: environmentalAlerts.severity,
      title: environmentalAlerts.title,
      body: environmentalAlerts.body,
      metadata: environmentalAlerts.metadata,
      isRead: environmentalAlerts.isRead,
      createdAt: environmentalAlerts.createdAt,
    })
    .from(environmentalAlerts)
    .innerJoin(users, eq(environmentalAlerts.userId, users.id))
    .where(
      and(
        eq(environmentalAlerts.isRead, false),
        // Only include alerts from the past 24 hours for the digest
        sql`${environmentalAlerts.createdAt} >= now() - interval '24 hours'`
      )
    )
    .orderBy(sql`${environmentalAlerts.userId}, ${environmentalAlerts.createdAt} DESC`);

  if (rows.length === 0) return { sent: 0 };

  // Group by userId
  const byUser = new Map<string, { email: string; alerts: typeof rows }>();
  for (const row of rows) {
    if (!byUser.has(row.userId)) {
      byUser.set(row.userId, { email: row.userEmail, alerts: [] });
    }
    byUser.get(row.userId)!.alerts.push(row);
  }

  let sent = 0;
  for (const [, { email, alerts }] of byUser) {
    const alertRecords = alerts.map((a) => ({
      id: a.alertId,
      userId: a.userId,
      alertType: a.alertType,
      severity: a.severity,
      title: a.title,
      body: a.body,
      metadata: a.metadata,
      isRead: a.isRead,
      createdAt: a.createdAt,
    }));

    try {
      await sendDigestEmail(email, alertRecords);
      sent += 1;
    } catch (err) {
      console.error(`[email-digest] Failed to send digest to ${email}:`, err);
    }
  }

  return { sent };
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
    const REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379";
    const connection = { host: "localhost", port: 6379 } as { host: string; port: number };

    try {
      const url = new URL(REDIS_URL);
      connection.host = url.hostname;
      connection.port = parseInt(url.port || "6379", 10);
    } catch {
      // keep defaults
    }

    const QUEUE_NAME = "email-digest";
    const queue = new bullmq.Queue(QUEUE_NAME, { connection });

    // Daily at 8am UTC
    queue
      .upsertJobScheduler(
        "email-digest-daily",
        { pattern: "0 8 * * *" },
        { name: "digest" }
      )
      .catch(() => {
        // Non-fatal — Redis may not be available during build
      });

    new bullmq.Worker(
      QUEUE_NAME,
      async () => {
        return runEmailDigestJob();
      },
      { connection }
    );
  }
}
