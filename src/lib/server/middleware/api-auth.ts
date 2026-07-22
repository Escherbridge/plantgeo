import { createHash } from "crypto";
import { db } from "@/lib/server/db";
import { apiKeys } from "@/lib/server/db/schema";
import { eq } from "drizzle-orm";
import Redis from "ioredis";
import { NextResponse } from "next/server";

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

type ApiKeyPrincipal = {
  keyId: string;
  userId?: string;
  teamId?: string;
  permissions: string[];
  rateLimit: number;
};

export type ApiKeyValidationResult =
  | ({ valid: true } & ApiKeyPrincipal)
  | { valid: false; error: string };

export type ApiKeyAuthorizationResult =
  | ({ valid: true } & ApiKeyPrincipal)
  | {
      valid: false;
      status: 401 | 403 | 429;
      error: string;
      retryAfter?: number;
    };

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
      permissions: apiKeys.permissions,
      rateLimit: apiKeys.rateLimit,
    })
    .from(apiKeys)
    .where(eq(apiKeys.keyHash, keyHash))
    .limit(1);

  if (record.length === 0) {
    return { valid: false, error: "Invalid API key" };
  }

  const { id, userId, teamId, permissions, rateLimit } = record[0];

  return {
    valid: true,
    keyId: id,
    userId: userId ?? undefined,
    teamId: teamId ?? undefined,
    permissions: Array.isArray(permissions)
      ? permissions.filter((permission): permission is string => typeof permission === "string")
      : [],
    rateLimit: rateLimit ?? 100,
  };
}

/** Enforces a v1 API-key permission and its per-key rate limit. */
export async function authorizeApiRequest(
  request: Request,
  requiredPermission: string
): Promise<ApiKeyAuthorizationResult> {
  const result = await validateApiKey(request);
  if (!result.valid) {
    return { valid: false, status: 401, error: result.error };
  }
  if (!result.permissions.includes(requiredPermission)) {
    return {
      valid: false,
      status: 403,
      error: "API key does not have permission for this endpoint",
    };
  }

  const rateLimit = await checkRateLimit(result.keyId, result.rateLimit);
  if (rateLimit.limited) {
    return {
      valid: false,
      status: 429,
      error: "Rate limit exceeded",
      retryAfter: rateLimit.retryAfter,
    };
  }

  return result;
}

/** Serializes a v1 API-key authorization failure without leaking key details. */
export function apiKeyAuthorizationErrorResponse(
  result: Extract<ApiKeyAuthorizationResult, { valid: false }>
): NextResponse {
  return NextResponse.json(
    { error: result.status === 401 ? "Invalid or missing API key" : result.error },
    {
      status: result.status,
      headers: result.retryAfter ? { "Retry-After": String(result.retryAfter) } : {},
    }
  );
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
