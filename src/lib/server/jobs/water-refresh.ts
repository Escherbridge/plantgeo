// npm install bullmq
// BullMQ job: refresh USGS streamflow gauges for Western USA and upsert to DB.
// Register this worker in Next.js instrumentation hook (instrumentation.ts):
//   import "@/lib/server/jobs/water-refresh";

import { db } from "@/lib/server/db";
import { waterGauges } from "@/lib/server/db/schema";
import { getStreamflowGauges } from "@/lib/server/services/usgs-water";
import { sql } from "drizzle-orm";

// Western USA bounding box: west,south,east,north
const WESTERN_USA_BBOX = "-125,31,-100,49";

/**
 * Fetch USGS streamflow gauges for Western USA and upsert to the waterGauges table.
 * On conflict (duplicate siteNo) the row is updated with the latest readings.
 */
export async function runWaterRefreshJob(): Promise<{ upserted: number }> {
  const gauges = await getStreamflowGauges(WESTERN_USA_BBOX);

  if (gauges.length === 0) {
    return { upserted: 0 };
  }

  await db
    .insert(waterGauges)
    .values(
      gauges.map((g) => ({
        siteNo: g.siteNo,
        siteName: g.siteName,
        lat: g.lat,
        lon: g.lon,
        flowCfs: g.flowCfs ?? undefined,
        percentile: g.percentile ?? undefined,
        trend: g.trend ?? undefined,
        condition: g.condition,
        updatedAt: new Date(),
      }))
    )
    .onConflictDoUpdate({
      target: waterGauges.siteNo,
      set: {
        flowCfs: sql`excluded.flow_cfs`,
        percentile: sql`excluded.percentile`,
        trend: sql`excluded.trend`,
        condition: sql`excluded.condition`,
        updatedAt: sql`now()`,
      },
    });

  return { upserted: gauges.length };
}

// ---------------------------------------------------------------------------
// BullMQ worker registration
// Guarded by typeof window check so this module is safe to import server-side
// without bundling BullMQ into the browser bundle.
// ---------------------------------------------------------------------------

if (typeof window === "undefined") {
  // Lazy-require so the build succeeds even when bullmq is not yet installed.
  let bullmq: typeof import("bullmq") | undefined;

  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    bullmq = require("bullmq");
  } catch {
    // bullmq not installed — skip worker registration
  }

  if (bullmq) {
    const REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379";
    const connection = { host: "localhost", port: 6379 } as { host: string; port: number };

    // Parse REDIS_URL for host/port
    try {
      const url = new URL(REDIS_URL);
      connection.host = url.hostname;
      connection.port = parseInt(url.port || "6379", 10);
    } catch {
      // keep defaults
    }

    const QUEUE_NAME = "water-refresh";

    // Ensure a repeating job exists (every 15 minutes)
    const queue = new bullmq.Queue(QUEUE_NAME, { connection });
    queue
      .upsertJobScheduler("water-refresh-15min", { every: 15 * 60 * 1000 }, { name: "refresh" })
      .catch(() => {
        // Non-fatal — Redis may not be available during build
      });

    // Worker processes jobs
    new bullmq.Worker(
      QUEUE_NAME,
      async () => {
        return runWaterRefreshJob();
      },
      { connection }
    );
  }
}
