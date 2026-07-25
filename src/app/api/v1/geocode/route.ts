import { NextRequest, NextResponse } from "next/server";
import {
  apiKeyAuthorizationErrorResponse,
  authorizeApiRequest,
} from "@/lib/server/middleware/api-auth";
import {
  ForwardGeocodeQuerySchema,
  forwardGeocode,
} from "@/lib/server/services/geocoding";
import {
  PRIVATE_EPHEMERAL_HEADERS,
  providerFailureResponse,
} from "@/lib/server/http/provider-response";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const authResult = await authorizeApiRequest(request, "read:geocode");
  if (!authResult.valid) return apiKeyAuthorizationErrorResponse(authResult);

  const parsed = ForwardGeocodeQuerySchema.safeParse(
    Object.fromEntries(request.nextUrl.searchParams.entries())
  );
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid geocoding query", details: parsed.error.flatten() },
      { status: 400, headers: PRIVATE_EPHEMERAL_HEADERS }
    );
  }

  try {
    const { q, ...options } = parsed.data;
    const data = await forwardGeocode(q, { ...options, lang: options.lang ?? "en" });
    return NextResponse.json(data, { headers: PRIVATE_EPHEMERAL_HEADERS });
  } catch (error) {
    return providerFailureResponse(error, "Geocoding service");
  }
}
