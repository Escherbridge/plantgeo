import Redis from "ioredis";

const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";

// Dedicated subscriber instance (cannot be reused for pub after subscribe)
let subscriber: Redis | null = null;

function getSubscriber(): Redis {
  if (!subscriber) {
    subscriber = new Redis(REDIS_URL, {
      maxRetriesPerRequest: 3,
      retryStrategy(times) {
        return Math.min(times * 50, 2000);
      },
    });
  }
  return subscriber;
}

// Publisher instance (shared, imported from redis.ts pattern)
let publisher: Redis | null = null;

function getPublisher(): Redis {
  if (!publisher) {
    publisher = new Redis(REDIS_URL, {
      maxRetriesPerRequest: 3,
      retryStrategy(times) {
        return Math.min(times * 50, 2000);
      },
    });
  }
  return publisher;
}

// Track active subscriptions: channel -> Set of callbacks
const subscriptions = new Map<string, Set<(data: unknown) => void>>();

/**
 * Publish data to a Redis channel.
 * Channels: layer:{layerId}, tracking:{assetId}, alerts:global
 */
export async function publish(channel: string, data: unknown): Promise<void> {
  const pub = getPublisher();
  await pub.publish(channel, JSON.stringify(data));
}

/**
 * Subscribe to a Redis channel with a callback.
 * Multiple callbacks per channel are supported.
 */
export async function subscribe(
  channel: string,
  callback: (data: unknown) => void
): Promise<void> {
  const sub = getSubscriber();

  if (!subscriptions.has(channel)) {
    subscriptions.set(channel, new Set());
    await sub.subscribe(channel);
  }

  subscriptions.get(channel)!.add(callback);

  // Set up message handler once
  sub.removeAllListeners("message");
  sub.on("message", (ch: string, message: string) => {
    const callbacks = subscriptions.get(ch);
    if (!callbacks) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(message);
    } catch {
      parsed = message;
    }
    for (const cb of callbacks) {
      cb(parsed);
    }
  });
}

/**
 * Unsubscribe a specific callback from a channel.
 * If no callbacks remain, unsubscribes from Redis channel.
 */
export async function unsubscribe(
  channel: string,
  callback?: (data: unknown) => void
): Promise<void> {
  const sub = getSubscriber();
  const callbacks = subscriptions.get(channel);

  if (!callbacks) return;

  if (callback) {
    callbacks.delete(callback);
  } else {
    callbacks.clear();
  }

  if (callbacks.size === 0) {
    subscriptions.delete(channel);
    await sub.unsubscribe(channel);
  }
}
