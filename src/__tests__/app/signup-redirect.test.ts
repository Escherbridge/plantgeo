import { describe, expect, it } from "vitest";
import nextConfig from "../../../next.config";

describe("signup aliases", () => {
  it("permanently redirects /signup and /sign-up to /register", async () => {
    const redirects = await nextConfig.redirects?.();
    expect(redirects).toEqual(
      expect.arrayContaining([
        { source: "/signup", destination: "/register", permanent: true },
        { source: "/sign-up", destination: "/register", permanent: true },
      ]),
    );
  });
});
