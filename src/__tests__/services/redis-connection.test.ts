import { describe, expect, it } from "vitest";
import { parseRedisConnectionOptions } from "@/lib/server/redis";

describe("parseRedisConnectionOptions", () => {
  it("preserves credentials, database selection, and TLS", () => {
    expect(
      parseRedisConnectionOptions(
        "rediss://queue%40user:p%40ss@example.internal:6380/3"
      )
    ).toEqual({
      host: "example.internal",
      port: 6380,
      username: "queue@user",
      password: "p@ss",
      db: 3,
      tls: { servername: "example.internal" },
    });
  });

  it("rejects unsupported protocols and invalid database paths", () => {
    expect(() => parseRedisConnectionOptions("http://localhost:6379")).toThrow();
    expect(() => parseRedisConnectionOptions("redis://localhost/not-a-db")).toThrow();
  });
});
