import { NextResponse } from "next/server";
import { runAllIngestionJobs } from "@/lib/server/services/ingestion-jobs";
import { authorizeCronRequest } from "@/lib/server/security/ingress";

export const maxDuration = 300; // 5 minutes max duration for Serverless execution
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const authorization = authorizeCronRequest(request);
    if (!authorization.authorized) {
      return NextResponse.json(
        { error: authorization.error },
        { status: authorization.status }
      );
    }

    const results = await runAllIngestionJobs();
    const failed = results.filter((result) => result.status === "failed");

    return NextResponse.json(
      {
        success: failed.length === 0,
        results,
      },
      { status: failed.length > 0 ? 502 : 200 }
    );
  } catch (error) {
    console.error("Ingestion Cron Error:", error);
    return NextResponse.json({ success: false, error: "Internal Server Error" }, { status: 500 });
  }
}
