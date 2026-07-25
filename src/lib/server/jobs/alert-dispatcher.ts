// BullMQ job: evaluate alert thresholds for all watched locations every 30 minutes.
// Started explicitly by the gated legacy worker role in instrumentation.ts.

import { db } from "@/lib/server/db";
import {
  watchedLocations,
  alertSubscriptions,
  environmentalAlerts,
  users,
} from "@/lib/server/db/schema";
import { eq, or } from "drizzle-orm";
import {
  checkFireProximityAlerts,
  checkDroughtAlerts,
  checkStreamflowAlerts,
  checkPriorityZoneAlerts,
} from "@/lib/server/services/alert-engine";
import { sendAlertEmail } from "@/lib/server/services/email";
import { publish } from "@/lib/server/services/realtime";
import { getRedisConnection } from "@/lib/server/redis";
import type { Queue, Worker } from "bullmq";

type CheckedAlert = Awaited<ReturnType<typeof checkFireProximityAlerts>>[number];

let dispatcherQueue: Queue | null = null;
let dispatcherWorker: Worker | null = null;

/** Produces the database-enforced identity for one UTC cooldown day. */
export function buildAlertDedupeKey(
  userId: string,
  alertType: string,
  locationId: string,
  now = new Date()
): string {
  return `${userId}:${alertType}:${locationId}:${now.toISOString().slice(0, 10)}`;
}

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
    .where(
      or(
        eq(alertSubscriptions.inAppEnabled, true),
        eq(alertSubscriptions.emailEnabled, true)
      )
    );

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
      const watchedLocationId = alert.metadata.watchedLocationId;
      if (typeof watchedLocationId !== "string") {
        console.error("[alert-dispatcher] Alert is missing its watched-location identity");
        continue;
      }

      let inserted: typeof environmentalAlerts.$inferSelect | undefined;
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
            dedupeKey: buildAlertDedupeKey(
              alert.userId,
              alert.alertType,
              watchedLocationId
            ),
            isRead: false,
          })
          .onConflictDoNothing({ target: environmentalAlerts.dedupeKey })
          .returning();
      } catch (err) {
        console.error("[alert-dispatcher] Failed to insert alert:", err);
        continue;
      }

      if (!inserted) continue;

      dispatched += 1;

      // Publish to Redis pub/sub for real-time SSE delivery
      if (row.inAppEnabled) {
        try {
          await publish(`alerts:${alert.userId}`, {
            event: "alert:new",
            alert: inserted,
          });
        } catch {
          // Realtime delivery is best-effort; the durable row remains available.
        }
      }

      // Each enabled subscription channel receives the durable alert.
      if (row.emailEnabled) {
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

/** Starts only when the explicitly gated legacy worker role calls it. */
export async function startAlertDispatcherWorker(): Promise<void> {
  if (dispatcherQueue || dispatcherWorker) return;

  const bullmq = await import("bullmq");
  const connection = getRedisConnection();
  const queue = new bullmq.Queue("alert-dispatcher", { connection });
  await queue.upsertJobScheduler(
    "alert-dispatcher-30min",
    { every: 30 * 60 * 1000 },
    { name: "dispatch" }
  );
  const worker = new bullmq.Worker(
    "alert-dispatcher",
    () => runAlertDispatcher(),
    { connection }
  );
  dispatcherQueue = queue;
  dispatcherWorker = worker;
}

export async function stopAlertDispatcherWorker(): Promise<void> {
  await dispatcherWorker?.close();
  await dispatcherQueue?.close();
  dispatcherWorker = null;
  dispatcherQueue = null;
}
