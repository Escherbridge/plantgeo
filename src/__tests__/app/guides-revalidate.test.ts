import { describe, expect, it } from "vitest";

import { revalidate } from "@/app/guides/layout";

describe("guides route segment", () => {
  it("revalidates the index and every child page hourly", () => {
    expect(revalidate).toBe(3600);
  });
});
