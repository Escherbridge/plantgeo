import { NextRequest, NextResponse } from "next/server";
import {
  ReverseGeocodeQuerySchema,
  normalizeResults,
  reverseGeocode,
} from "@/lib/server/services/geocoding";
import { enforcePublicProviderRateLimit } from "@/lib/server/security/public-provider-rate-limit";
import {
  PRIVATE_EPHEMERAL_HEADERS,
  providerFailureResponse,
  publicRateLimitFailureResponse,
} from "@/lib/server/http/provider-response";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const parsed = ReverseGeocodeQuerySchema.safeParse(
    Object.fromEntries(request.nextUrl.searchParams.entries())
  );
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid reverse-geocoding query", details: parsed.error.flatten() },
      { status: 400, headers: PRIVATE_EPHEMERAL_HEADERS }
    );
  }

  const rateLimit = await enforcePublicProviderRateLimit(request, "reverse-geocode", 20);
  if (!rateLimit.allowed) return publicRateLimitFailureResponse(rateLimit);

  try {
    const { lat, lon, ...options } = parsed.data;
    const result = await reverseGeocode(lat, lon, options);
    return NextResponse.json(
      { results: normalizeResults(result) },
      { headers: PRIVATE_EPHEMERAL_HEADERS }
    );
  } catch (error) {
    return providerFailureResponse(error, "Reverse-geocoding service");
  }
}
