import { NextRequest, NextResponse } from "next/server";
import {
  apiKeyAuthorizationErrorResponse,
  authorizeApiRequest,
} from "@/lib/server/middleware/api-auth";
import { parseBoundedJson } from "@/lib/server/security/ingress";
import { getRoute, routeRequestSchema } from "@/lib/server/services/routing";
import {
  PRIVATE_EPHEMERAL_HEADERS,
  providerFailureResponse,
} from "@/lib/server/http/provider-response";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const authResult = await authorizeApiRequest(request, "read:routes");
  if (!authResult.valid) return apiKeyAuthorizationErrorResponse(authResult);

  const body = await parseBoundedJson(request, 32 * 1024);
  if (!body.ok) {
    return NextResponse.json(
      { error: body.error },
      { status: body.status, headers: PRIVATE_EPHEMERAL_HEADERS }
    );
  }

  const parsed = routeRequestSchema.safeParse(body.data);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid routing request", details: parsed.error.flatten() },
      { status: 422, headers: PRIVATE_EPHEMERAL_HEADERS }
    );
  }

  try {
    const result = await getRoute(parsed.data);
    return NextResponse.json(result.rawResponse, { headers: PRIVATE_EPHEMERAL_HEADERS });
  } catch (error) {
    return providerFailureResponse(error, "Routing service");
  }
}
