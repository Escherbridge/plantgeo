import { NextRequest, NextResponse } from "next/server";
import {
  ForwardGeocodeQuerySchema,
  forwardGeocode,
  normalizeResults,
} from "@/lib/server/services/geocoding";
import { enforcePublicProviderRateLimit } from "@/lib/server/security/public-provider-rate-limit";
import {
  PRIVATE_EPHEMERAL_HEADERS,
  providerFailureResponse,
  publicRateLimitFailureResponse,
} from "@/lib/server/http/provider-response";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const parsed = ForwardGeocodeQuerySchema.safeParse(
    Object.fromEntries(request.nextUrl.searchParams.entries())
  );
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid geocoding query", details: parsed.error.flatten() },
      { status: 400, headers: PRIVATE_EPHEMERAL_HEADERS }
    );
  }

  const rateLimit = await enforcePublicProviderRateLimit(request, "geocode", 30);
  if (!rateLimit.allowed) return publicRateLimitFailureResponse(rateLimit);

  try {
    const { q, ...options } = parsed.data;
    const result = await forwardGeocode(q, options);
    return NextResponse.json(
      { results: normalizeResults(result) },
      { headers: PRIVATE_EPHEMERAL_HEADERS }
    );
  } catch (error) {
    return providerFailureResponse(error, "Geocoding service");
  }
}
