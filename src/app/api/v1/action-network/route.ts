import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** See docs/data-ingestion-and-serving-contract.md §Location and freshness boundaries. */
export async function GET() {
  return NextResponse.json(
    {
      error: {
        code: "ACTION_NETWORK_INACTIVE",
      message:
        "Action-network waypoints are inactive until a reviewed partner-scoped warehouse publication is available",
        retryable: false,
      },
    },
    {
      status: 503,
      headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    }
  );
}
