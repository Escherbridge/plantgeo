import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const instance = {
    connect: vi.fn(() => Promise.resolve()),
    on: vi.fn(),
    publish: vi.fn(() => Promise.resolve(1)),
    subscribe: vi.fn<() => Promise<number>>(),
    unsubscribe: vi.fn(() => Promise.resolve(0)),
  };
  return {
    instance,
    Redis: vi.fn(() => instance),
  };
});

vi.mock("ioredis", () => ({ default: mocks.Redis }));

import { subscribe, unsubscribe } from "@/lib/server/services/realtime";

describe("realtime subscription setup", () => {
  it("propagates Redis failure and retries without poisoned channel state", async () => {
    const callback = vi.fn();
    mocks.instance.subscribe
      .mockRejectedValueOnce(new Error("Redis unavailable"))
      .mockResolvedValueOnce(1);

    await expect(subscribe("layer:test", callback)).rejects.toThrow(
      "Redis unavailable"
    );
    await expect(subscribe("layer:test", callback)).resolves.toBeUndefined();
    expect(mocks.instance.subscribe).toHaveBeenCalledTimes(2);

    await unsubscribe("layer:test", callback);
  });
});
