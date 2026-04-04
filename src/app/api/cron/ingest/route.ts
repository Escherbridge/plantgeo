import { NextResponse } from "next/server";
import { runAllIngestionJobs } from "@/lib/server/services/ingestion-jobs";

export const maxDuration = 300; // 5 minutes max duration for Serverless execution
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const secret = searchParams.get("secret");

    // Basic authorization check for cron job
    if (process.env.CRON_SECRET && secret !== process.env.CRON_SECRET) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    await runAllIngestionJobs();

    return NextResponse.json({ success: true, message: "Ingestion jobs completed successfully." });
  } catch (error) {
    console.error("Ingestion Cron Error:", error);
    return NextResponse.json({ success: false, error: "Internal Server Error" }, { status: 500 });
  }
}
