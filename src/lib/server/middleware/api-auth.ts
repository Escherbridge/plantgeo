import { createHash } from "crypto";
import { db } from "@/lib/server/db";
import { apiKeys } from "@/lib/server/db/schema";
import { eq } from "drizzle-orm";
import Redis from "ioredis";

const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";

let redis: Redis | null = null;

function getRedis(): Redis {
  if (!redis) {
    redis = new Redis(REDIS_URL, {
      maxRetriesPerRequest: 3,
      retryStrategy(times) {
        return Math.min(times * 50, 2000);
      },
    });
  }
  return redis;
}

export interface ApiKeyValidationResult {
  valid: boolean;
  keyId?: string;
  userId?: string;
  teamId?: string;
  rateLimit?: number;
  error?: string;
}

/**
 * Validate an API key from the X-Api-Key (or x-api-key) request header.
 * Hashes the key with SHA-256 and looks it up in the apiKeys table.
 * Returns the key record on success, or an error string on failure.
 */
export async function validateApiKey(
  request: Request
): Promise<ApiKeyValidationResult> {
  const key =
    request.headers.get("x-api-key") ?? request.headers.get("X-Api-Key");

  if (!key) {
    return { valid: false, error: "Missing X-Api-Key header" };
  }

  const keyHash = createHash("sha256").update(key).digest("hex");

  const record = await db
    .select({
      id: apiKeys.id,
      userId: apiKeys.userId,
      teamId: apiKeys.teamId,
      rateLimit: apiKeys.rateLimit,
    })
    .from(apiKeys)
    .where(eq(apiKeys.keyHash, keyHash))
    .limit(1);

  if (record.length === 0) {
    return { valid: false, error: "Invalid API key" };
  }

  const { id, userId, teamId, rateLimit } = record[0];

  return {
    valid: true,
    keyId: id,
    userId: userId ?? undefined,
    teamId: teamId ?? undefined,
    rateLimit: rateLimit ?? 100,
  };
}

/**
 * Check rate limit using a Redis sliding window counter.
 * Key: ratelimit:{keyId}:{minuteTimestamp}
 * Increments counter for the current minute; returns true if limit exceeded.
 * Default limit: 100 requests/minute.
 */
export async function checkRateLimit(
  keyId: string,
  limitPerMinute = 100
): Promise<{ limited: boolean; retryAfter?: number }> {
  const minuteTimestamp = Math.floor(Date.now() / 60_000);
  const redisKey = `ratelimit:${keyId}:${minuteTimestamp}`;

  try {
    const r = getRedis();
    const count = await r.incr(redisKey);
    // Set expiry on first request of this window
    if (count === 1) {
      await r.expire(redisKey, 60);
    }

    if (count > limitPerMinute) {
      // Seconds remaining in the current minute window
      const secondsElapsed = Math.floor((Date.now() % 60_000) / 1000);
      const retryAfter = 60 - secondsElapsed;
      return { limited: true, retryAfter };
    }

    return { limited: false };
  } catch {
    // Redis unavailable — allow the request through
    return { limited: false };
  }
}
