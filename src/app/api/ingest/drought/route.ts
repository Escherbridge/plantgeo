import { NextRequest, NextResponse } from "next/server";
import { authorizeIngressRequest } from "@/lib/server/security/ingress";
import { ingestDroughtRelease } from "@/lib/server/services/drought-ingestion";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The national collection is ~19 MB and PostGIS repairs every ring on write.
export const maxDuration = 300;

/**
 * GET /api/ingest/drought[?date=YYYY-MM-DD][&force=1]
 * Fetches a US Drought Monitor weekly release and stores it as PostGIS geometry.
 * Omit `date` to take the newest published release. `force` re-writes a release
 * that is already stored, for the rare case where USDM corrects one.
 */
export async function GET(request: NextRequest) {
  const authorization = authorizeIngressRequest(request);
  if (!authorization.authorized) {
    return NextResponse.json(
      { error: authorization.error },
      { status: authorization.status }
    );
  }

  const { searchParams } = new URL(request.url);
  const date = searchParams.get("date");
  if (date !== null && !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return NextResponse.json(
      { error: "date must be an ISO YYYY-MM-DD USDM Tuesday" },
      { status: 422 }
    );
  }

  try {
    const outcome = await ingestDroughtRelease({
      validDate: date ?? undefined,
      replace: searchParams.get("force") === "1",
    });
    return NextResponse.json({ ok: true, ...outcome });
  } catch (err) {
    // A malformed date reaches here as a RangeError from the upstream client.
    if (err instanceof RangeError) {
      return NextResponse.json({ error: err.message }, { status: 422 });
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json(
      { error: "Failed to ingest drought release", details: message },
      { status: 502 }
    );
  }
}
