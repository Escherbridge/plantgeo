// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Exercises the real cache and pub/sub helpers in `src/lib/server/redis.ts` against
 * `ioredis-mock`, an in-memory stand-in for the wire protocol `redis-connection.test.ts`
 * deliberately never touches (that file covers only `parseRedisConnectionOptions`, pure URL
 * parsing with no client involved).
 *
 * `ioredis-mock` instances share ONE in-memory store by default -- confirmed by hand before
 * writing this file, since it's the one fact this whole suite's isolation depends on -- which
 * is what makes it possible to publish through the module's singleton client and subscribe
 * through a second, independently constructed one. `vi.resetModules()` in `beforeEach` gives
 * every case both a fresh `redis.ts` (so `redisAvailable` and the singleton client reset) and a
 * fresh `ioredis-mock` backing store, and `flushall()` in `afterEach` is the belt-and-suspenders
 * cleanup for whichever store the case actually touched.
 */

/** Loads the mock class the same way `vi.mock` below resolves the real `ioredis` import. */
async function loadRedisMockClass() {
  const mockModule = await import("ioredis-mock");
  return mockModule.default;
}

vi.mock("ioredis", async () => {
  const RedisMock = (await import("ioredis-mock")).default;
  return { default: RedisMock };
});

describe("redis cache and pub/sub operations", () => {
  let redisModule: typeof import("@/lib/server/redis");

  beforeEach(async () => {
    vi.resetModules();
    redisModule = await import("@/lib/server/redis");
  });

  afterEach(async () => {
    const RedisMock = await loadRedisMockClass();
    await new RedisMock().flushall();
    await redisModule.closeRedis();
    vi.restoreAllMocks();
  });

  const sampleGeoJSON = {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [-116.2, 43.6] },
        properties: { network: "ASOS", sensorId: "KBOI" },
      },
    ],
  };

  it("round-trips a GeoJSON payload through cacheGeoJSON and getCachedGeoJSON", async () => {
    await redisModule.cacheGeoJSON("layer:sensors:0:0:0", sampleGeoJSON, 300);
    const cached = await redisModule.getCachedGeoJSON("layer:sensors:0:0:0");
    expect(cached).toEqual(sampleGeoJSON);
  });

  it("returns null on a cache miss rather than throwing", async () => {
    const cached = await redisModule.getCachedGeoJSON("layer:never-written");
    expect(cached).toBeNull();
  });

  it("applies the requested TTL, and 300s when none is given", async () => {
    await redisModule.cacheGeoJSON("layer:custom-ttl", { a: 1 }, 42);
    await redisModule.cacheGeoJSON("layer:default-ttl", { a: 1 });

    const r = redisModule.getRedis();
    expect(await r.ttl("layer:custom-ttl")).toBe(42);
    expect(await r.ttl("layer:default-ttl")).toBe(300);
  });

  it("serializes the GeoJSON to JSON on write and parses it back on read", async () => {
    const r = redisModule.getRedis();
    await redisModule.cacheGeoJSON("layer:raw-check", sampleGeoJSON, 60);

    const raw = await r.get("layer:raw-check");
    expect(raw).toBe(JSON.stringify(sampleGeoJSON));
    expect(await redisModule.getCachedGeoJSON("layer:raw-check")).toEqual(sampleGeoJSON);
  });

  it("delivers a published event to a subscriber's message callback", async () => {
    const RedisMock = await loadRedisMockClass();
    const subscriber = new RedisMock();

    const received = new Promise<{ channel: string; message: string }>((resolve) => {
      subscriber.on("message", (channel: string, message: string) => {
        resolve({ channel, message });
      });
    });

    await subscriber.subscribe("geo:fire-alerts");
    await redisModule.publishGeoEvent("geo:fire-alerts", {
      fireId: "OR4420112150020200817",
      severity: "high",
    });

    const { channel, message } = await received;
    expect(channel).toBe("geo:fire-alerts");
    expect(JSON.parse(message)).toEqual({
      fireId: "OR4420112150020200817",
      severity: "high",
    });

    await subscriber.quit();
  });

  it("never delivers to a subscriber on a different channel", async () => {
    const RedisMock = await loadRedisMockClass();
    const subscriber = new RedisMock();
    const messages: string[] = [];
    subscriber.on("message", (_channel: string, message: string) => messages.push(message));
    await subscriber.subscribe("geo:other-channel");

    await redisModule.publishGeoEvent("geo:fire-alerts", { fireId: "should-not-arrive" });
    // No await-able signal for "nothing happened"; a microtask flush is the honest wait here.
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(messages).toHaveLength(0);
    await subscriber.quit();
  });

  /**
   * The module's own degradation contract, read from `redis.ts` before writing this: every
   * write and read path wraps its command in try/catch and swallows the failure -- a cache
   * write failure must never surface to a caller building a tile response, and a read failure
   * must resolve `null` exactly like a genuine cache miss would.
   *
   * `ioredis-mock` installs its commands as OWN properties on each instance at construction
   * time (`_initCommands`), not on the shared prototype, so the throwing client is built by
   * spying on the module's own singleton -- the same instance `getRedis()` hands every helper.
   */
  describe("error-path degradation", () => {
    it("cacheGeoJSON swallows a write failure and resolves without throwing", async () => {
      const client = redisModule.getRedis();
      const setexSpy = vi.spyOn(client, "setex").mockRejectedValue(new Error("ECONNREFUSED"));

      await expect(
        redisModule.cacheGeoJSON("layer:will-fail", sampleGeoJSON)
      ).resolves.toBeUndefined();
      expect(setexSpy).toHaveBeenCalled();
    });

    it("getCachedGeoJSON swallows a read failure and resolves null, indistinguishable from a miss", async () => {
      const client = redisModule.getRedis();
      vi.spyOn(client, "get").mockRejectedValue(new Error("ECONNREFUSED"));

      await expect(redisModule.getCachedGeoJSON("layer:will-fail")).resolves.toBeNull();
    });

    it("publishGeoEvent swallows a publish failure and resolves without throwing", async () => {
      const client = redisModule.getRedis();
      const publishSpy = vi
        .spyOn(client, "publish")
        .mockRejectedValue(new Error("ECONNREFUSED"));

      await expect(
        redisModule.publishGeoEvent("geo:will-fail", { a: 1 })
      ).resolves.toBeUndefined();
      expect(publishSpy).toHaveBeenCalled();
    });
  });
});
