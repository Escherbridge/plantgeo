// Shared BullMQ queue/scheduler/worker registration for the simple cron-style jobs under
// `jobs/*.ts`. See `src/lib/server/AGENTS.md` §jobs for why the per-job manual repeatable-job
// cleanup these callers used to hand-roll was replaced by `upsertJobScheduler`'s own upsert.

import { getRedisConnection } from "@/lib/server/redis";
import type { Job, Queue as BullQueue, Worker as BullWorker } from "bullmq";

export interface BullmqCronRegistration<DataType, ResultType> {
  queue: BullQueue<DataType>;
  worker: BullWorker<DataType, ResultType>;
}

export interface RegisterBullmqCronOptions<DataType, ResultType> {
  /** BullMQ queue name — also the worker's queue name. */
  queueName: string;
  /** Job template name BullMQ stamps on each produced job. */
  jobName: string;
  /** Standard five-field cron pattern passed to `upsertJobScheduler`. */
  cronPattern: string;
  /** Runs once per scheduled tick. */
  handler: (job: Job<DataType, ResultType>) => Promise<ResultType>;
}

/**
 * Lazily requires `bullmq` (absent in some deploy targets) and, when present, registers one
 * repeatable job via `upsertJobScheduler` plus a `Worker` that runs `handler`. The scheduler id
 * is `${queueName}-daily`, matching the convention `conversation-cleanup.ts` and
 * `email-digest.ts` already used before this extraction. Returns `null` when `bullmq` cannot be
 * loaded, so callers can skip registration the same way they used to.
 */
export function registerBullmqCron<DataType = unknown, ResultType = unknown>(
  options: RegisterBullmqCronOptions<DataType, ResultType>
): BullmqCronRegistration<DataType, ResultType> | null {
  const { queueName, jobName, cronPattern, handler } = options;

  let bullmq: typeof import("bullmq") | undefined;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    bullmq = require("bullmq");
  } catch {
    return null; // bullmq not installed — skip registration
  }
  if (!bullmq) return null;

  const connection = getRedisConnection();
  const queue = new bullmq.Queue<DataType>(queueName, { connection });

  // The untyped view: BullMQ's typed Queue narrows scheduler ids to the job-name union.
  (queue as BullQueue)
    .upsertJobScheduler(`${queueName}-daily`, { pattern: cronPattern }, { name: jobName })
    .catch(() => {
      // Non-fatal — Redis may not be available during build
    });

  const worker = new bullmq.Worker<DataType, ResultType>(queueName, handler, { connection });

  return { queue, worker };
}
