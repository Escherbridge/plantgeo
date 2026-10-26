// BullMQ job: send daily email digest to users with unread alerts.
// Cron: 8am UTC daily.
// Register in Next.js instrumentation hook (instrumentation.ts):
//   import "@/lib/server/jobs/email-digest";

import { db } from "@/lib/server/db";
import { environmentalAlerts, users } from "@/lib/server/db/schema";
import { eq, and, sql } from "drizzle-orm";
import { sendDigestEmail } from "@/lib/server/services/email";
import { registerBullmqCron } from "@/lib/server/jobs/shared";

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
  // Daily at 8am UTC. See `src/lib/server/jobs/shared.ts` for the registration helper.
  registerBullmqCron({
    queueName: "email-digest",
    jobName: "digest",
    cronPattern: "0 8 * * *",
    handler: async () => runEmailDigestJob(),
  });
}
