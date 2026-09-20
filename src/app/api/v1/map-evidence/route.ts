import { NextRequest, NextResponse } from "next/server";
import { enforcePublicProviderRateLimit } from "@/lib/server/security/public-provider-rate-limit";
import { publicRateLimitFailureResponse } from "@/lib/server/http/provider-response";
import { readBoundedAppMapEvidence } from "@/lib/server/services/regional-map-evidence";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function readBody(request: NextRequest): Promise<string | null> {
  if (!request.body) return "";
  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  let size = 0;
  let body = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return body + decoder.decode();
      size += value.byteLength;
      if (size > 32 * 1024) {
        await reader.cancel();
        return null;
      }
      body += decoder.decode(value, { stream: true });
    }
  } finally {
    reader.releaseLock();
  }
}

/** Public, bounded application-layer tile evidence shared with the MCP reader. */
export async function POST(request: NextRequest) {
  const rate = await enforcePublicProviderRateLimit(request, "map-evidence", 30);
  if (!rate.allowed) return publicRateLimitFailureResponse(rate);
  const body = await readBody(request);
  if (body === null) {
    return NextResponse.json({ error: "request_too_large" }, { status: 413 });
  }
  let args: unknown;
  try { args = JSON.parse(body); } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return NextResponse.json({ error: "invalid_selection" }, { status: 400 });
  }
  const result = await readBoundedAppMapEvidence(args as Record<string, unknown>, request.signal);
  return NextResponse.json(result, {
    status: result.error === "invalid_selection" ? 400 : 200,
    headers: { "Cache-Control": "no-store" },
  });
}
