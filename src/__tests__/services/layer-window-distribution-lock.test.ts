// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The only fake is the Redis wire: `ioredis` itself is swapped for `ioredis-mock` (the same
 * pattern as `redis-operations.test.ts`), an in-memory client whose `eval` runs a real Lua VM
 * (fengari). `redis.ts`'s `RELEASE_IF_OWNER_SCRIPT` compare-and-delete therefore executes for real here, not
 * a hand-rolled JS stand-in for it -- mutate the script (e.g. drop the GET check, or always DEL)
 * and the second case below goes red. See services/AGENTS.md §window-distribution.
 */
vi.mock("ioredis", async () => {
  const RedisMock = (await import("ioredis-mock")).default;
  return { default: RedisMock };
});

describe("releaseCacheLock", () => {
  let redisModule: typeof import("@/lib/server/redis");

  beforeEach(async () => {
    vi.resetModules();
    redisModule = await import("@/lib/server/redis");
  });

  afterEach(async () => {
    const RedisMock = (await import("ioredis-mock")).default;
    await new RedisMock().flushall();
    await redisModule.closeRedis();
    vi.restoreAllMocks();
  });

  it("deletes the lock while it still carries this caller's own token", async () => {
    const key = "layer-window-distribution:v2:x:lock";
    const redis = redisModule.getRedis();
    await redis.set(key, "token-A");

    await redisModule.releaseCacheLock(key, "token-A");

    expect(await redis.get(key)).toBeNull();
  });

  it("does not delete a lock another holder re-acquired after this caller's own lock expired", async () => {
    const key = "layer-window-distribution:v2:x:lock";
    const redis = redisModule.getRedis();
    // This caller held "token-A"; it expired and TTL already cleared the key, then another
    // in-flight request re-acquired the same key with "token-B" before this caller's release ran.
    // The old GET-then-DEL race could delete that second holder's lock; the atomic script must not.
    await redis.set(key, "token-B");

    await redisModule.releaseCacheLock(key, "token-A");

    expect(await redis.get(key)).toBe("token-B");
  });
});
