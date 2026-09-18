import { describe, expect, it } from "vitest";
import { PILOT_STATES } from "@/lib/server/services/land-context/budgets";
import { PNW_STATE_CODES } from "@/lib/server/db/schema/land-context/shared";
import { getRegion, REGION_SUBDIVISION_CODES } from "@/lib/region/region";

describe("region subdivision codes", () => {
  it("are derived from the manifest's admin codes, not restated beside them", () => {
    const expected = getRegion().adminCodes.map((adminCode) => adminCode.split("-")[1]);
    expect(REGION_SUBDIVISION_CODES).toEqual(expected);
    expect(REGION_SUBDIVISION_CODES).toEqual(["WA", "OR", "ID"]);
  });

  it("are the ONE definition both former restatements now read", () => {
    // `PNW_STATE_CODES` (Drizzle side) and `PILOT_STATES` (reader side) were two hand-written
    // copies of the same three codes; a next region changing `admin_codes` used to leave the
    // manifest, the alias and the Parquet row schema disagreeing (STYLE-REVIEW-W2 S1/S2).
    expect(PNW_STATE_CODES).toBe(REGION_SUBDIVISION_CODES);
    expect(PILOT_STATES).toBe(REGION_SUBDIVISION_CODES);
  });
});
