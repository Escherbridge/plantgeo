import { describe, expect, it } from "vitest";
import { PNW_STATE_CODES } from "@/lib/server/db/schema/land-context/shared";
import { getRegion } from "@/lib/region/region";

describe("land-context shared PNW_STATE_CODES", () => {
  it("pins the deprecated alias to the region manifest's admin codes", () => {
    const expected = getRegion().adminCodes.map((adminCode) => adminCode.split("-")[1]);
    expect(PNW_STATE_CODES).toEqual(expected);
    expect(PNW_STATE_CODES).toEqual(["WA", "OR", "ID"]);
  });
});
