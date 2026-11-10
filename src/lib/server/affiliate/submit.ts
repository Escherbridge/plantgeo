import { checkRateLimit } from "@/lib/server/middleware/api-auth";
import {
  AFFILIATE_APPLICATIONS_PER_MINUTE,
  affiliateApplicationRateLimitKey,
  decideAffiliateApplication,
  type AffiliateApplicationRow,
} from "@/lib/server/security/affiliate-application";

export type AffiliateSubmission =
  | { status: 204 }
  | { status: 200 }
  | { status: 400 }
  | { status: 429; retryAfter: number }
  | { status: 503 };

/**
 * Shared by the public POST route and the tRPC procedure. The honeypot
 * outcome is a 204 with no row. The limiter failing closed is a 503.
 */
export async function submitAffiliateApplication(
  request: Request,
  payload: unknown,
  insert: (row: AffiliateApplicationRow) => Promise<void>
): Promise<AffiliateSubmission> {
  const rate = await checkRateLimit(
    affiliateApplicationRateLimitKey(request),
    AFFILIATE_APPLICATIONS_PER_MINUTE
  );
  const decision = decideAffiliateApplication({ payload, rate });
  if (decision.outcome === "silent") return { status: 204 };
  if (decision.outcome === "invalid") return { status: 400 };
  if (decision.outcome === "unavailable") return { status: 503 };
  if (decision.outcome === "rate_limited") {
    return { status: 429, retryAfter: decision.retryAfter };
  }
  await insert(decision.row);
  return { status: 200 };
}
