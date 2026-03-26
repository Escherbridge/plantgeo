// npm install bullmq
import { recomputePriorityZones } from "@/lib/server/services/priority-zones";

const QUEUE_NAME = "priority-zone-refresh";
const REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379";

// Parse Redis connection options from URL
function getRedisConnection() {
  try {
    const url = new URL(REDIS_URL);
    return {
      host: url.hostname,
      port: parseInt(url.port || "6379", 10),
      password: url.password || undefined,
      tls: url.protocol === "rediss:" ? {} : undefined,
    };
  } catch {
    return { host: "localhost", port: 6379 };
  }
}

import type { Queue as BullQueue, Worker as BullWorker } from "bullmq";

let queue: BullQueue | null = null;
let worker: BullWorker | null = null;

export async function startJobs(): Promise<void> {
  let bullmq: typeof import("bullmq") | undefined;

  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    bullmq = require("bullmq");
  } catch {
    // bullmq not installed — skip job registration
    return;
  }

  if (!bullmq) return;

  const connection = getRedisConnection();

  queue = new bullmq.Queue(QUEUE_NAME, { connection });

  // Remove existing repeatable jobs and register fresh
  const repeatableJobs = await queue.getRepeatableJobs();
  for (const job of repeatableJobs) {
    await queue.removeRepeatableByKey(job.key);
  }

  // Schedule nightly at 2am UTC
  await queue.add(
    "recompute",
    {},
    {
      repeat: { pattern: "0 2 * * *" },
    }
  );

  worker = new bullmq.Worker(
    QUEUE_NAME,
    async () => {
      await recomputePriorityZones();
    },
    { connection }
  );

  worker.on("completed", () => {
    console.log("[priority-zone-refresh] Priority zones recomputed successfully");
  });

  worker.on("failed", (_job: unknown, err: unknown) => {
    console.error("[priority-zone-refresh] Job failed:", err);
  });
}

export async function stopJobs(): Promise<void> {
  await worker?.close();
  await queue?.close();
}
