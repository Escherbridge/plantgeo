import { NextRequest, NextResponse } from "next/server";
import {
  apiKeyAuthorizationErrorResponse,
  authorizeApiRequest,
} from "@/lib/server/middleware/api-auth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const PARTNER_DIRECTORY_INACTIVE_MESSAGE =
  "Partner discovery is inactive until verified organizations and access rules are published";

/** Keeps private workspace details out of global API-key discovery. */
export async function GET(request: NextRequest) {
  const authResult = await authorizeApiRequest(request, "read:teams");
  if (!authResult.valid) return apiKeyAuthorizationErrorResponse(authResult);

  return NextResponse.json(
    {
      code: "PARTNER_DIRECTORY_INACTIVE",
      error: PARTNER_DIRECTORY_INACTIVE_MESSAGE,
      retryable: false,
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
