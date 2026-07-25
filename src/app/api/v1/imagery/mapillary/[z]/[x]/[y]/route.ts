import { NextResponse } from "next/server";
import {
  PRIVATE_EPHEMERAL_HEADERS,
  publicRateLimitFailureResponse,
} from "@/lib/server/http/provider-response";
import { enforcePublicProviderRateLimit } from "@/lib/server/security/public-provider-rate-limit";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const MAX_TILE_BYTES = 5 * 1024 * 1024;

async function readBounded(response: Response): Promise<Uint8Array<ArrayBuffer>> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Mapillary tile response was empty");
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_TILE_BYTES) {
      await reader.cancel();
      throw new Error("Mapillary tile exceeded the response limit");
    }
    chunks.push(value);
  }
  const result = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

export async function GET(
  request: Request,
  context: { params: Promise<{ z: string; x: string; y: string }> }
) {
  const token = process.env.MAPILLARY_ACCESS_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: "Imagery coverage is not configured" },
      { status: 503, headers: { "Cache-Control": "no-store" } }
    );
  }

  const raw = await context.params;
  const zoom = Number(raw.z);
  const x = Number(raw.x);
  const y = Number(raw.y);
  const maximumIndex = 2 ** zoom;
  if (
    !Number.isInteger(zoom) ||
    zoom < 6 ||
    zoom > 14 ||
    !Number.isInteger(x) ||
    !Number.isInteger(y) ||
    x < 0 ||
    y < 0 ||
    x >= maximumIndex ||
    y >= maximumIndex
  ) {
    return NextResponse.json(
      { error: "Invalid tile coordinate" },
      { status: 400, headers: { "Cache-Control": "no-store" } }
    );
  }

  const rateLimit = await enforcePublicProviderRateLimit(
    request,
    "imagery-mapillary-tile",
    180
  );
  if (!rateLimit.allowed) return publicRateLimitFailureResponse(rateLimit);

  try {
    const upstream = await fetch(
      `https://tiles.mapillary.com/maps/vtp/mly1_public/2/${zoom}/${x}/${y}?access_token=${encodeURIComponent(
        token
      )}`,
      { signal: AbortSignal.timeout(10_000), cache: "no-store" }
    );
    if (!upstream.ok) throw new Error(`Mapillary returned ${upstream.status}`);
    const tile = await readBounded(upstream);
    return new Response(tile.buffer, {
      headers: {
        "Cache-Control": "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400",
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/vnd.mapbox-vector-tile",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return NextResponse.json(
      { error: "Imagery coverage is temporarily unavailable" },
      {
        status: 502,
        headers: { ...PRIVATE_EPHEMERAL_HEADERS, "Retry-After": "30" },
      }
    );
  }
}
