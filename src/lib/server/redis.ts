import Redis from "ioredis";

const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";

let redis: Redis | null = null;

export function getRedis(): Redis {
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

export async function publishGeoEvent(
  channel: string,
  data: Record<string, unknown>
) {
  const r = getRedis();
  await r.publish(channel, JSON.stringify(data));
}

export async function cacheGeoJSON(
  key: string,
  data: unknown,
  ttlSeconds: number = 300
) {
  const r = getRedis();
  await r.setex(key, ttlSeconds, JSON.stringify(data));
}

export async function getCachedGeoJSON<T>(key: string): Promise<T | null> {
  const r = getRedis();
  const cached = await r.get(key);
  return cached ? JSON.parse(cached) : null;
}
