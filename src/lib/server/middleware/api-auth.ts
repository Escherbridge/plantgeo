import { db } from "@/lib/server/db";
import { apiKeys, teamMembers } from "@/lib/server/db/schema";
import { and, eq, isNull, lt, or, sql } from "drizzle-orm";
import Redis from "ioredis";
import { NextResponse } from "next/server";
import {
  hashApiKey,
  hasRequiredApiKeyPermission,
  isApiKeyPermission,
  isSupportedApiKey,
  shouldRefreshApiKeyLastUsed,
  API_KEY_LAST_USED_REFRESH_MS,
  type ApiKeyPermission,
} from "@/lib/server/api-keys";
import { verifyPassword } from "@/lib/server/password";

const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

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

interface ApiKeyPrincipal {
  keyId: string;
  userId?: string;
  teamId?: string;
  permissions: ApiKeyPermission[];
  rateLimit: number;
}

export type ApiKeyValidationResult =
  | ({ valid: true } & ApiKeyPrincipal)
  | { valid: false; error: string };

export type ApiKeyAuthorizationFailure = {
  valid: false;
  status: 401 | 403 | 429 | 503;
  error: string;
  retryAfter?: number;
} & Partial<ApiKeyPrincipal>;

export type ApiKeyAuthorizationSuccess = { valid: true } & ApiKeyPrincipal;

export type ApiKeyAuthorizationResult =
  | ApiKeyAuthorizationSuccess
  | ApiKeyAuthorizationFailure;

export type ApiKeyRateLimitResult =
  | { available: false }
  | { available: true; limited: false }
  | { available: true; limited: true; retryAfter: number };

/** Converts limiter availability into a typed, fail-closed authorization result. */
export function applyApiKeyRateLimit(
  principal: ApiKeyAuthorizationSuccess,
  result: ApiKeyRateLimitResult
): ApiKeyAuthorizationResult {
  if (!result.available) {
    return {
      ...principal,
      valid: false,
      status: 503,
      error: "API key request limiter is unavailable",
      retryAfter: 30,
    };
  }
  if (result.limited) {
    return {
      ...principal,
      valid: false,
      status: 429,
      error: "Rate limit exceeded",
      retryAfter: result.retryAfter,
    };
  }
  return principal;
}

/**
 * Validates API keys with a fixed-length SHA-256 lookup.
 */
export async function validateApiKey(
  request: Request
): Promise<ApiKeyValidationResult> {
  const key = request.headers.get("x-api-key");

  if (!key || !isSupportedApiKey(key)) {
    return { valid: false, error: "Invalid API key" };
  }

  const keyHash = hashApiKey(key);

  const records = await db
    .select({
      id: apiKeys.id,
      userId: apiKeys.userId,
      teamId: apiKeys.teamId,
      permissions: apiKeys.permissions,
      rateLimit: apiKeys.rateLimit,
      keyHash: apiKeys.keyHash,
      lastUsed: apiKeys.lastUsed,
    })
    .from(apiKeys)
    .where(eq(apiKeys.keyHash, keyHash))
    .limit(1);

  let record = records[0];

  // See docs/deployment.md §API-key digest migration.
  const legacyKeyId = request.headers.get("x-legacy-api-key-id");
  if (
    !record &&
    process.env.ALLOW_LEGACY_BCRYPT_API_KEYS === "true" &&
    legacyKeyId &&
    UUID_PATTERN.test(legacyKeyId)
  ) {
    const legacyRecord = await db
      .select({
        id: apiKeys.id,
        userId: apiKeys.userId,
        teamId: apiKeys.teamId,
        permissions: apiKeys.permissions,
        rateLimit: apiKeys.rateLimit,
        keyHash: apiKeys.keyHash,
        lastUsed: apiKeys.lastUsed,
      })
      .from(apiKeys)
      .where(
        and(
          eq(apiKeys.id, legacyKeyId),
          sql`${apiKeys.keyHash} LIKE '$2%'`
        )
      )
      .limit(1);

    if (legacyRecord[0] && (await verifyPassword(key, legacyRecord[0].keyHash))) {
      record = legacyRecord[0];
      await db
        .update(apiKeys)
        .set({ keyHash })
        .where(eq(apiKeys.id, legacyRecord[0].id));
    }
  }

  if (!record) {
    return { valid: false, error: "Invalid API key" };
  }

  const {
    id,
    userId,
    teamId,
    permissions: storedPermissions,
    rateLimit,
    lastUsed,
  } = record;
  if (teamId) {
    if (!userId) return { valid: false, error: "Invalid API key" };
    const [membership] = await db
      .select({ teamId: teamMembers.teamId })
      .from(teamMembers)
      .where(
        and(eq(teamMembers.teamId, teamId), eq(teamMembers.userId, userId))
      )
      .limit(1);
    if (!membership) return { valid: false, error: "Invalid API key" };
  }
  const permissions = Array.isArray(storedPermissions)
    ? storedPermissions.filter(
        (permission): permission is ApiKeyPermission =>
          typeof permission === "string" && isApiKeyPermission(permission)
      )
    : [];

  const now = new Date();
  if (shouldRefreshApiKeyLastUsed(lastUsed, now)) {
    try {
      const cutoff = new Date(now.getTime() - API_KEY_LAST_USED_REFRESH_MS);
      await db
        .update(apiKeys)
        .set({ lastUsed: now })
        .where(
          and(
            eq(apiKeys.id, id),
            or(isNull(apiKeys.lastUsed), lt(apiKeys.lastUsed, cutoff))
          )
        );
    } catch {
      // Best-effort only. Authentication has already established the key is valid.
    }
  }

  return {
    valid: true,
    keyId: id,
    userId: userId ?? undefined,
    teamId: teamId ?? undefined,
    permissions,
    rateLimit: rateLimit ?? 100,
  };
}

/** Checks both the requested scope and the per-key rate limit for v1 routes. */
export async function authorizeApiRequest(
  request: Request,
  requiredPermission: ApiKeyPermission
): Promise<ApiKeyAuthorizationResult> {
  const authResult = await validateApiKey(request);
  if (!authResult.valid) {
    return { ...authResult, status: 401 };
  }

  if (!hasRequiredApiKeyPermission(authResult.permissions, requiredPermission)) {
    return {
      ...authResult,
      valid: false,
      status: 403,
      error: "API key does not have permission for this endpoint",
    };
  }

  const rateLimitResult = await checkRateLimit(
    authResult.keyId,
    authResult.rateLimit
  );
  return applyApiKeyRateLimit(authResult, rateLimitResult);
}

/** Converts a centralized API-key authorization failure into a v1 response. */
export function apiKeyAuthorizationErrorResponse(
  result: ApiKeyAuthorizationFailure
): NextResponse {
  const status = result.status;
  const error =
    status === 401 ? "Invalid or missing API key" : result.error ?? "Unauthorized";
  return NextResponse.json(
    { error },
    {
      status,
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
): Promise<ApiKeyRateLimitResult> {
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
      return { limited: true, available: true, retryAfter };
    }

    return { limited: false, available: true };
  } catch {
    // Authorization converts limiter outages into a fail-closed 503 response.
    return { available: false };
  }
}
