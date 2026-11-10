import { z } from "zod";
import type { ApiKeyRateLimitResult } from "@/lib/server/middleware/api-auth";
import { ipRateLimitKey } from "@/lib/server/security/registration";

export const AFFILIATE_APPLICATIONS_PER_MINUTE = 5;
export const AFFILIATE_HONEYPOT_FIELD = "fax_number";
export const MAX_AFFILIATE_APPLICATION_BODY_BYTES = 8_192;

const AFFILIATE_ROLES = ["designer", "extension", "installer", "school-garden", "other"] as const;

export type AffiliateApplicationRow = {
  name: string;
  email: string;
  organization: string | null;
  role: string | null;
  website: string | null;
  message: string | null;
  status: "pending";
};

export type AffiliateDecision =
  | { outcome: "unavailable" }
  | { outcome: "rate_limited"; retryAfter: number }
  | { outcome: "silent" }
  | { outcome: "invalid" }
  | { outcome: "store"; row: AffiliateApplicationRow };

function optionalText(max: number) {
  return z
    .union([z.string().trim().max(max), z.literal("")])
    .optional()
    .transform((value) => (value ? value : null));
}

const affiliateApplicationSchema = z
  .object({
    name: z.string().trim().min(1).max(200),
    email: z.string().trim().toLowerCase().email().max(254),
    organization: optionalText(200),
    role: z
      .union([z.enum(AFFILIATE_ROLES), z.literal("")])
      .optional()
      .transform((value) => (value ? value : null)),
    website: z
      .union([z.string().trim().max(500), z.literal("")])
      .optional()
      .superRefine((value, context) => {
        if (!value) return;
        let url: URL;
        try {
          url = new URL(value);
        } catch {
          context.addIssue({ code: "custom", message: "Website must be an http(s) URL" });
          return;
        }
        if (url.protocol !== "http:" && url.protocol !== "https:") {
          context.addIssue({ code: "custom", message: "Website must be an http(s) URL" });
        }
      })
      .transform((value) => (value ? new URL(value).toString() : null)),
    message: optionalText(4_000),
    [AFFILIATE_HONEYPOT_FIELD]: z.string().max(500).optional(),
  })
  .strict();

export function affiliateApplicationRateLimitKey(request: Request): string {
  return ipRateLimitKey(request, "affiliate:apply");
}

/** A filled honeypot, or a non-string stand-in, is a bot. An empty string is a person. */
export function honeypotTripped(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return value.trim() !== "";
  return true;
}

function readHoneypot(payload: unknown): unknown {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) return undefined;
  return (payload as Record<string, unknown>)[AFFILIATE_HONEYPOT_FIELD];
}

/**
 * Limiter first, then the honeypot, then field checks. A tripped honeypot is
 * silent and stores nothing. Redis being down fails closed for everyone.
 */
export function decideAffiliateApplication(input: {
  payload: unknown;
  rate: ApiKeyRateLimitResult;
}): AffiliateDecision {
  if (!input.rate.available) return { outcome: "unavailable" };
  if (input.rate.limited) return { outcome: "rate_limited", retryAfter: input.rate.retryAfter };
  if (honeypotTripped(readHoneypot(input.payload))) return { outcome: "silent" };

  const parsed = affiliateApplicationSchema.safeParse(input.payload);
  if (!parsed.success) return { outcome: "invalid" };

  return {
    outcome: "store",
    row: {
      name: parsed.data.name,
      email: parsed.data.email,
      organization: parsed.data.organization,
      role: parsed.data.role,
      website: parsed.data.website,
      message: parsed.data.message,
      status: "pending",
    },
  };
}
