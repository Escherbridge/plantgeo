export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  // Import-time BullMQ workers are quarantined from web replicas; see docs.
  if (process.env.ENABLE_LEGACY_BULLMQ_JOBS !== "true") return;
  if (process.env.SERVICE_ROLE !== "legacy-background-worker") {
    console.error(
      "[instrumentation] legacy jobs require SERVICE_ROLE=legacy-background-worker"
    );
    return;
  }

  const { probeRedis } = await import("@/lib/server/redis");
  if (!(await probeRedis())) {
    console.error("[instrumentation] Redis unavailable; legacy jobs not started");
    return;
  }

  const { startJobs } = await import("@/lib/server/jobs/priority-zone-refresh");
  await startJobs();
  await import("@/lib/server/jobs/water-refresh");
  const { startAlertDispatcherWorker } = await import(
    "@/lib/server/jobs/alert-dispatcher"
  );
  await startAlertDispatcherWorker();
  await import("@/lib/server/jobs/email-digest");
  await import("@/lib/server/jobs/conversation-cleanup");
}
