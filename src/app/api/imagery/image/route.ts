import { NextRequest, NextResponse } from "next/server";
import { getImageById, MapillaryImageQuerySchema } from "@/lib/server/services/mapillary";
import { enforcePublicProviderRateLimit } from "@/lib/server/security/public-provider-rate-limit";
import {
  PRIVATE_EPHEMERAL_HEADERS,
  providerFailureResponse,
  publicRateLimitFailureResponse,
} from "@/lib/server/http/provider-response";

export const dynamic = "force-dynamic";

const LEGACY_HEADERS = { ...PRIVATE_EPHEMERAL_HEADERS, Deprecation: "true" } as const;

export async function GET(request: NextRequest) {
  const parsed = MapillaryImageQuerySchema.safeParse(
    Object.fromEntries(request.nextUrl.searchParams.entries())
  );
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid imagery identifier", details: parsed.error.flatten() },
      { status: 400, headers: LEGACY_HEADERS }
    );
  }

  const rateLimit = await enforcePublicProviderRateLimit(request, "imagery-image", 60);
  if (!rateLimit.allowed) return publicRateLimitFailureResponse(rateLimit);

  try {
    const image = await getImageById(parsed.data.id);
    return NextResponse.json(image, { headers: LEGACY_HEADERS });
  } catch (error) {
    return providerFailureResponse(error, "Imagery service");
  }
}
