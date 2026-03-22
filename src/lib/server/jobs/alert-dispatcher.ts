// BullMQ job: evaluate alert thresholds for all watched locations every 30 minutes.
// Register in Next.js instrumentation hook (instrumentation.ts):
//   import "@/lib/server/jobs/alert-dispatcher";

import { db } from "@/lib/server/db";
import {
  watchedLocations,
  alertSubscriptions,
  environmentalAlerts,
  users,
} from "@/lib/server/db/schema";
import { eq, and } from "drizzle-orm";
import {
  checkFireProximityAlerts,
  checkDroughtAlerts,
  checkStreamflowAlerts,
  checkPriorityZoneAlerts,
} from "@/lib/server/services/alert-engine";
import { sendAlertEmail } from "@/lib/server/services/email";
import { publish } from "@/lib/server/services/realtime";

type CheckedAlert = Awaited<ReturnType<typeof checkFireProximityAlerts>>[number];

/**
 * Run all alert checks for a single watched location + subscription pair.
 */
async function runChecksForSubscription(
  location: {
    id: string;
    userId: string;
    lat: number;
    lon: number;
    radiusKm: number | null;
    name: string;
  },
  subscription: {
    alertType: string;
    emailEnabled: boolean | null;
    inAppEnabled: boolean | null;
  }
): Promise<CheckedAlert[]> {
  const radiusKm = location.radiusKm ?? 50;

  switch (subscription.alertType) {
    case "fire_proximity":
      return checkFireProximityAlerts(location.userId, location.id, location.lat, location.lon, radiusKm);
    case "drought_escalation":
      return checkDroughtAlerts(location.userId, location.id, location.lat, location.lon);
    case "streamflow_critical":
      return checkStreamflowAlerts(location.userId, location.id, location.lat, location.lon);
    case "priority_zone_created":
      return checkPriorityZoneAlerts(location.userId, location.id, location.lat, location.lon, radiusKm);
    default:
      return [];
  }
}

/**
 * Main dispatcher: evaluate all subscriptions and persist/deliver new alerts.
 */
export async function runAlertDispatcher(): Promise<{ dispatched: number }> {
  // Load all subscriptions joined with their watched locations and user email
  const subscriptionRows = await db
    .select({
      locationId: watchedLocations.id,
      locationName: watchedLocations.name,
      userId: watchedLocations.userId,
      lat: watchedLocations.lat,
      lon: watchedLocations.lon,
      radiusKm: watchedLocations.radiusKm,
      alertType: alertSubscriptions.alertType,
      emailEnabled: alertSubscriptions.emailEnabled,
      inAppEnabled: alertSubscriptions.inAppEnabled,
      userEmail: users.email,
    })
    .from(alertSubscriptions)
    .innerJoin(watchedLocations, eq(alertSubscriptions.watchedLocationId, watchedLocations.id))
    .innerJoin(users, eq(watchedLocations.userId, users.id))
    .where(eq(alertSubscriptions.inAppEnabled, true));

  let dispatched = 0;

  for (const row of subscriptionRows) {
    let newAlerts: CheckedAlert[] = [];
    try {
      newAlerts = await runChecksForSubscription(
        {
          id: row.locationId,
          userId: row.userId,
          lat: row.lat,
          lon: row.lon,
          radiusKm: row.radiusKm,
          name: row.locationName,
        },
        {
          alertType: row.alertType,
          emailEnabled: row.emailEnabled,
          inAppEnabled: row.inAppEnabled,
        }
      );
    } catch (err) {
      console.error(`[alert-dispatcher] Check failed for ${row.alertType} @ ${row.locationId}:`, err);
      continue;
    }

    for (const alert of newAlerts) {
      // Persist to DB
      let inserted;
      try {
        [inserted] = await db
          .insert(environmentalAlerts)
          .values({
            userId: alert.userId,
            alertType: alert.alertType,
            severity: alert.severity,
            title: alert.title,
            body: alert.body,
            metadata: alert.metadata,
            isRead: false,
          })
          .returning();
      } catch (err) {
        console.error("[alert-dispatcher] Failed to insert alert:", err);
        continue;
      }

      dispatched += 1;

      // Publish to Redis pub/sub for real-time SSE delivery
      try {
        await publish(`alerts:${alert.userId}`, {
          event: "alert:new",
          alert: inserted,
        });
      } catch {
        // Non-fatal — SSE delivery is best-effort
      }

      // Send immediate email for high-severity alerts if emailEnabled
      if (row.emailEnabled && (alert.severity === "critical" || alert.severity === "warning")) {
        try {
          await sendAlertEmail(row.userEmail, inserted);
        } catch (err) {
          console.error("[alert-dispatcher] Email send failed:", err);
        }
      }
    }
  }

  return { dispatched };
}

// ---------------------------------------------------------------------------
// BullMQ worker registration — server-side only
// ---------------------------------------------------------------------------

if (typeof window === "undefined") {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let Worker: any | undefined;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let Queue: any | undefined;

  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    ({ Worker, Queue } = require("bullmq"));
  } catch {
    // bullmq not installed — skip
  }

  if (Worker && Queue) {
    const REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379";
    const connection = { host: "localhost", port: 6379 } as { host: string; port: number };

    try {
      const url = new URL(REDIS_URL);
      connection.host = url.hostname;
      connection.port = parseInt(url.port || "6379", 10);
    } catch {
      // keep defaults
    }

    const QUEUE_NAME = "alert-dispatcher";
    const queue = new Queue(QUEUE_NAME, { connection });

    // Every 30 minutes
    queue
      .upsertJobScheduler("alert-dispatcher-30min", { every: 30 * 60 * 1000 }, { name: "dispatch" })
      .catch(() => {
        // Non-fatal — Redis may not be available during build
      });

    new Worker(
      QUEUE_NAME,
      async () => {
        return runAlertDispatcher();
      },
      { connection }
    );
  }
}
