import { createHash } from "crypto";
import { getRedis } from "@/lib/server/redis";

type RateLimitResult =
  | { allowed: true; remaining: number }
  | { allowed: false; status: 429 | 503; retryAfter: number };

const developmentCounters = new Map<string, { count: number; expiresAt: number }>();

function requestFingerprint(request: Request): string {
  const forwardedFor = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  const address =
    request.headers.get("cf-connecting-ip")?.trim() ||
    request.headers.get("x-real-ip")?.trim() ||
    forwardedFor ||
    "unknown";
  return createHash("sha256").update(address.slice(0, 128)).digest("hex").slice(0, 24);
}

function localRateLimit(key: string, limit: number, windowSeconds: number): RateLimitResult {
  const now = Date.now();
  const existing = developmentCounters.get(key);
  const counter =
    !existing || existing.expiresAt <= now
      ? { count: 0, expiresAt: now + windowSeconds * 1_000 }
      : existing;
  counter.count += 1;
  developmentCounters.set(key, counter);
  const retryAfter = Math.max(1, Math.ceil((counter.expiresAt - now) / 1_000));
  return counter.count > limit
    ? { allowed: false, status: 429, retryAfter }
    : { allowed: true, remaining: limit - counter.count };
}

/** Rate-limit anonymous provider access; production fails closed without Redis. */
export async function enforcePublicProviderRateLimit(
  request: Request,
  bucket: string,
  limit: number,
  windowSeconds = 60
): Promise<RateLimitResult> {
  const window = Math.floor(Date.now() / (windowSeconds * 1_000));
  const key = `public-provider:${bucket}:${requestFingerprint(request)}:${window}`;

  try {
    const redis = getRedis();
    const result = await redis
      .multi()
      .incr(key)
      .expire(key, windowSeconds + 5)
      .exec();
    const count = Number(result?.[0]?.[1]);
    if (!Number.isSafeInteger(count) || count < 1) throw new Error("Invalid rate count");
    const retryAfter = windowSeconds - Math.floor((Date.now() / 1_000) % windowSeconds);
    return count > limit
      ? { allowed: false, status: 429, retryAfter: Math.max(1, retryAfter) }
      : { allowed: true, remaining: limit - count };
  } catch {
    if (process.env.NODE_ENV === "production") {
      return { allowed: false, status: 503, retryAfter: 30 };
    }
    return localRateLimit(key, limit, windowSeconds);
  }
}
