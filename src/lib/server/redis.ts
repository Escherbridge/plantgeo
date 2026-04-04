import Redis from "ioredis";

const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";

let redis: Redis | null = null;
let redisAvailable = true;
let lastWarning = 0;

export function getRedis(): Redis {
  if (!redis) {
    redis = new Redis(REDIS_URL, {
      maxRetriesPerRequest: 1,
      lazyConnect: true,
      retryStrategy(times) {
        if (times > 3) {
          redisAvailable = false;
          // Log at most once per 60 seconds to avoid spam
          const now = Date.now();
          if (now - lastWarning > 60_000) {
            lastWarning = now;
            console.warn("[redis] Redis unavailable — caching and pub/sub disabled");
          }
          return null; // stop retrying
        }
        return Math.min(times * 200, 2000);
      },
    });

    redis.on("connect", () => {
      redisAvailable = true;
    });

    redis.on("error", () => {
      // Suppress — retryStrategy handles logging
    });

    redis.connect().catch(() => {
      redisAvailable = false;
    });
  }
  return redis;
}

export function isRedisAvailable(): boolean {
  return redisAvailable;
}

export async function publishGeoEvent(
  channel: string,
  data: Record<string, unknown>
) {
  if (!redisAvailable) return;
  try {
    const r = getRedis();
    await r.publish(channel, JSON.stringify(data));
  } catch {
    // Redis offline — skip publish
  }
}

export async function cacheGeoJSON(
  key: string,
  data: unknown,
  ttlSeconds: number = 300
) {
  if (!redisAvailable) return;
  try {
    const r = getRedis();
    await r.setex(key, ttlSeconds, JSON.stringify(data));
  } catch {
    // Redis offline — skip cache write
  }
}

export async function getCachedGeoJSON<T>(key: string): Promise<T | null> {
  if (!redisAvailable) return null;
  try {
    const r = getRedis();
    const cached = await r.get(key);
    return cached ? JSON.parse(cached) : null;
  } catch {
    return null;
  }
}

/**
 * Parse REDIS_URL into host/port for BullMQ connection config.
 * Returns null if Redis is known to be unavailable.
 */
export function getRedisConnection(): { host: string; port: number } | null {
  const connection = { host: "localhost", port: 6379 };
  try {
    const url = new URL(REDIS_URL);
    connection.host = url.hostname;
    connection.port = parseInt(url.port || "6379", 10);
  } catch {
    // keep defaults
  }
  return connection;
}

/**
 * Probe whether Redis is reachable. Returns true if a PING succeeds within
 * the timeout, false otherwise. Uses a disposable connection.
 */
export async function probeRedis(timeoutMs = 2000): Promise<boolean> {
  return new Promise((resolve) => {
    const probe = new Redis(REDIS_URL, {
      lazyConnect: true,
      connectTimeout: timeoutMs,
      maxRetriesPerRequest: 0,
      retryStrategy() {
        return null;
      },
    });
    probe.on("error", () => { /* suppress */ });

    const timer = setTimeout(() => {
      probe.disconnect();
      resolve(false);
    }, timeoutMs);

    probe.connect()
      .then(() => probe.ping())
      .then(() => {
        clearTimeout(timer);
        probe.disconnect();
        resolve(true);
      })
      .catch(() => {
        clearTimeout(timer);
        probe.disconnect();
        resolve(false);
      });
  });
}
