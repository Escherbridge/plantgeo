import Redis from "ioredis";

const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";

function createResilientRedis(): Redis {
  const instance = new Redis(REDIS_URL, {
    maxRetriesPerRequest: 1,
    lazyConnect: true,
    retryStrategy(times) {
      if (times > 3) return null;
      return Math.min(times * 200, 2000);
    },
  });
  instance.on("error", () => {
    // Connection failures are reported to required callers.
  });
  instance.connect().catch(() => {
    // Lazy operations retry or reject according to their own contract.
  });
  return instance;
}

let subscriber: Redis | null = null;
let publisher: Redis | null = null;

const subscriptions = new Map<string, Set<(data: unknown) => void>>();
const subscriptionSetups = new Map<string, Promise<void>>();

function dispatchMessage(channel: string, message: string): void {
  const callbacks = subscriptions.get(channel);
  if (!callbacks) return;
  let parsed: unknown;
  try {
    parsed = JSON.parse(message);
  } catch {
    parsed = message;
  }
  for (const callback of callbacks) callback(parsed);
}

function getSubscriber(): Redis {
  if (!subscriber) {
    subscriber = createResilientRedis();
    subscriber.on("message", dispatchMessage);
  }
  return subscriber;
}

function getPublisher(): Redis {
  if (!publisher) publisher = createResilientRedis();
  return publisher;
}

/** Publishes best-effort realtime data without claiming delivery. */
export async function publish(channel: string, data: unknown): Promise<void> {
  try {
    const pub = getPublisher();
    await pub.publish(channel, JSON.stringify(data));
  } catch {
    // Optional realtime delivery must not roll back durable writes.
  }
}

/** Publishes when the caller must distinguish Redis failure from success. */
export async function publishRequired(channel: string, data: unknown): Promise<void> {
  const pub = getPublisher();
  await pub.publish(channel, JSON.stringify(data));
}

/** Subscribes only after Redis confirms the channel and propagates setup failure. */
export async function subscribe(
  channel: string,
  callback: (data: unknown) => void
): Promise<void> {
  const activeCallbacks = subscriptions.get(channel);
  if (activeCallbacks) {
    activeCallbacks.add(callback);
    return;
  }

  const sub = getSubscriber();
  let setup = subscriptionSetups.get(channel);
  if (!setup) {
    setup = sub.subscribe(channel).then(() => {
      subscriptions.set(channel, new Set());
    });
    subscriptionSetups.set(channel, setup);
    const clearSetup = () => {
      if (subscriptionSetups.get(channel) === setup) {
        subscriptionSetups.delete(channel);
      }
    };
    void setup.then(clearSetup, clearSetup);
  }

  await setup;
  const callbacks = subscriptions.get(channel);
  if (!callbacks) throw new Error("Realtime subscription state is unavailable");
  callbacks.add(callback);
}

/** Removes one callback and releases the Redis channel when none remain. */
export async function unsubscribe(
  channel: string,
  callback?: (data: unknown) => void
): Promise<void> {
  const callbacks = subscriptions.get(channel);
  if (!callbacks) return;

  if (callback) callbacks.delete(callback);
  else callbacks.clear();

  if (callbacks.size === 0) {
    subscriptions.delete(channel);
    try {
      await getSubscriber().unsubscribe(channel);
    } catch {
      // The local subscription is still removed when Redis is unavailable.
    }
  }
}
