export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { startJobs } = await import("@/lib/server/jobs/priority-zone-refresh");
    await startJobs();

    await import("@/lib/server/jobs/water-refresh");
    await import("@/lib/server/jobs/alert-dispatcher");
    await import("@/lib/server/jobs/email-digest");
  }
}
