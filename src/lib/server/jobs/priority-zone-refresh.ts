import {
  PRIORITY_ZONE_RECOMPUTATION_STATE,
  recomputePriorityZones,
} from "@/lib/server/services/priority-zones";
import { registerBullmqCron } from "@/lib/server/jobs/shared";
import type { Queue as BullQueue, Worker as BullWorker } from "bullmq";

const QUEUE_NAME = "priority-zone-refresh";

let queue: BullQueue | null = null;
let worker: BullWorker | null = null;

export async function startJobs(): Promise<void> {
  if (PRIORITY_ZONE_RECOMPUTATION_STATE === "inactive") {
    console.info("[priority-zone-refresh] disabled pending reviewed publication");
    return;
  }

  // Nightly at 2am UTC. `registerBullmqCron`'s `upsertJobScheduler` call replaces the
  // getRepeatableJobs/removeRepeatableByKey cleanup this job used to hand-roll on every start —
  // see `src/lib/server/AGENTS.md` §jobs.
  const registration = registerBullmqCron({
    queueName: QUEUE_NAME,
    jobName: "recompute",
    cronPattern: "0 2 * * *",
    handler: async () => {
      await recomputePriorityZones();
    },
  });
  if (!registration) return; // bullmq not installed — skip job registration

  queue = registration.queue;
  worker = registration.worker;

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
