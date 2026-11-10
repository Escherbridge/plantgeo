import { affiliateApplications } from "@/lib/server/db/schema";
import { db } from "@/lib/server/db";
import { submitAffiliateApplication } from "@/lib/server/affiliate/submit";
import { MAX_AFFILIATE_APPLICATION_BODY_BYTES } from "@/lib/server/security/affiliate-application";
import { parseBoundedJson } from "@/lib/server/security/ingress";

export async function POST(request: Request): Promise<Response> {
  const body = await parseBoundedJson(request, MAX_AFFILIATE_APPLICATION_BODY_BYTES);
  if (!body.ok) {
    return Response.json({ error: body.error }, { status: body.status });
  }

  const result = await submitAffiliateApplication(request, body.data, async (row) => {
    await db.insert(affiliateApplications).values(row);
  });

  if (result.status === 204) return new Response(null, { status: 204 });
  if (result.status === 200) return Response.json({ received: true });
  if (result.status === 429) {
    return Response.json(
      { error: "Too many applications from this network. Try again in a minute." },
      { status: 429, headers: { "Retry-After": String(result.retryAfter) } }
    );
  }
  if (result.status === 503) {
    return Response.json(
      { error: "Applications are temporarily unavailable. Try again shortly." },
      { status: 503 }
    );
  }
  return Response.json(
    { error: "Check the name, email, and website, then try again." },
    { status: 400 }
  );
}
