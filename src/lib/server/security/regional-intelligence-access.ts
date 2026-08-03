import { randomUUID } from "crypto";
import { getRedis } from "@/lib/server/redis";

/** Serving contract state; see docs/data-ingestion-and-serving-contract.md. */
export const REGIONAL_INTELLIGENCE_SERVING_STATE = "active" as const;

export const REGIONAL_INTELLIGENCE_INACTIVE_MESSAGE =
  "Regional intelligence is paused while its serving contract is being revised";

export const REGIONAL_INTELLIGENCE_QUOTA_MESSAGE =
  "You have used your regional intelligence requests for this window";

export const REGIONAL_INTELLIGENCE_UNMETERED_MESSAGE =
  "Regional intelligence cannot be metered right now, so it is unavailable";

export type EntitlementTier = "signed_in" | "paid" | "partner";

export interface EntitlementPolicy {
  tier: EntitlementTier;
  /** Requests permitted inside one rolling window. */
  requestsPerWindow: number;
  windowMs: number;
}

const WINDOW_MS = 3_600_000;

const TIER_POLICIES: Record<EntitlementTier, EntitlementPolicy> = {
  signed_in: { tier: "signed_in", requestsPerWindow: 20, windowMs: WINDOW_MS },
  paid: { tier: "paid", requestsPerWindow: 200, windowMs: WINDOW_MS },
  partner: { tier: "partner", requestsPerWindow: 1_000, windowMs: WINDOW_MS },
};

/**
 * Billing seam. Every signed-in account currently resolves to the `signed_in`
 * tier; the auth/org workstream replaces this body with a real subscription and
 * partner-agreement lookup without touching the reservation logic below.
 */
export async function resolveEntitlementPolicy(
  _userId: string
): Promise<EntitlementPolicy> {
  return TIER_POLICIES.signed_in;
}

export interface RegionalIntelligenceEntitlement {
  allowed: boolean;
  reason: string;
  tier: EntitlementTier;
  remaining: number;
  retryAfterSeconds: number | null;
  resetAt: string | null;
}

/**
 * Atomically trims the window, counts, and only then records the reservation —
 * a read-then-write pair would let concurrent replicas both pass at the limit.
 * Returns `{granted, count, oldestScore}`.
 */
const RESERVE_SCRIPT = `
  redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[2])
  local count = redis.call('ZCARD', KEYS[1])
  if count >= tonumber(ARGV[3]) then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    return { 0, count, oldest[2] or '0' }
  end
  redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
  redis.call('EXPIRE', KEYS[1], ARGV[5])
  return { 1, count + 1, '0' }
`;

function usageKey(userId: string): string {
  return `regional-intelligence:usage:${userId}`;
}

/**
 * Reserves one durable usage slot before any context assembly or model call.
 * Fails closed in production when the meter is unreachable: an unmetered agent
 * is an uncapped bill.
 */
export async function reserveRegionalIntelligenceUsage(
  userId: string
): Promise<RegionalIntelligenceEntitlement> {
  const policy = await resolveEntitlementPolicy(userId);
  const now = Date.now();

  try {
    const [granted, count, oldestScore] = (await getRedis().eval(
      RESERVE_SCRIPT,
      1,
      usageKey(userId),
      now,
      now - policy.windowMs,
      policy.requestsPerWindow,
      `${now}:${randomUUID()}`,
      Math.ceil(policy.windowMs / 1_000)
    )) as [number, number, string];

    if (Number(granted) === 1) {
      return {
        allowed: true,
        reason: "",
        tier: policy.tier,
        remaining: Math.max(0, policy.requestsPerWindow - Number(count)),
        retryAfterSeconds: null,
        resetAt: null,
      };
    }

    const windowResetAt = Number(oldestScore) + policy.windowMs;
    return {
      allowed: false,
      reason: REGIONAL_INTELLIGENCE_QUOTA_MESSAGE,
      tier: policy.tier,
      remaining: 0,
      retryAfterSeconds: Math.max(1, Math.ceil((windowResetAt - now) / 1_000)),
      resetAt: new Date(windowResetAt).toISOString(),
    };
  } catch {
    const allowUnmetered = process.env.NODE_ENV !== "production";
    return {
      allowed: allowUnmetered,
      reason: allowUnmetered ? "" : REGIONAL_INTELLIGENCE_UNMETERED_MESSAGE,
      tier: policy.tier,
      remaining: 0,
      retryAfterSeconds: allowUnmetered ? null : 60,
      resetAt: null,
    };
  }
}

export interface RegionalIntelligenceUsageStatus {
  tier: EntitlementTier;
  limit: number;
  remaining: number;
  resetAt: string | null;
}

/** Read-only quota view for the UI; never consumes a slot. */
export async function readRegionalIntelligenceUsage(
  userId: string
): Promise<RegionalIntelligenceUsageStatus> {
  const policy = await resolveEntitlementPolicy(userId);
  const now = Date.now();

  try {
    const redis = getRedis();
    const key = usageKey(userId);
    await redis.zremrangebyscore(key, "-inf", now - policy.windowMs);
    const used = await redis.zcard(key);
    const oldest = await redis.zrange(key, 0, 0, "WITHSCORES");
    const oldestScore = Number(oldest[1] ?? 0);

    return {
      tier: policy.tier,
      limit: policy.requestsPerWindow,
      remaining: Math.max(0, policy.requestsPerWindow - used),
      resetAt:
        used > 0 && Number.isFinite(oldestScore)
          ? new Date(oldestScore + policy.windowMs).toISOString()
          : null,
    };
  } catch {
    return {
      tier: policy.tier,
      limit: policy.requestsPerWindow,
      remaining: 0,
      resetAt: null,
    };
  }
}
