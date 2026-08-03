import { NextResponse } from "next/server";
import { runAllIngestionJobs } from "@/lib/server/services/ingestion-jobs";
import { authorizeCronRequest } from "@/lib/server/security/ingress";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// A full ingestion outlives edge/proxy connection timeouts (~100s), so the
// route acknowledges immediately and runs the jobs detached; the long-running
// Node server keeps executing after the response is sent. Results land in the
// server log. The flag serializes runs on the single-replica deployment.
let ingestionInFlight = false;

export async function GET(request: Request) {
  const authorization = authorizeCronRequest(request);
  if (!authorization.authorized) {
    return NextResponse.json(
      { error: authorization.error },
      { status: authorization.status }
    );
  }

  if (ingestionInFlight) {
    return NextResponse.json(
      { status: "already_running" },
      { status: 409 }
    );
  }

  ingestionInFlight = true;
  void runAllIngestionJobs()
    .then((results) => {
      const failed = results.filter((result) => result.status === "failed");
      console.log(
        `Ingestion cron finished (${failed.length} failed):`,
        JSON.stringify(results)
      );
    })
    .catch((error) => {
      console.error("Ingestion Cron Error:", error);
    })
    .finally(() => {
      ingestionInFlight = false;
    });

  return NextResponse.json({ status: "started" }, { status: 202 });
}
