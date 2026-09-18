import { describe, expect, it } from "vitest";
import { PILOT_STATES } from "@/lib/server/services/land-context/budgets";
import { PNW_STATE_CODES } from "@/lib/server/db/schema/land-context/shared";
import {
  assertAdminCodesMatchDeclaredTuple,
  getRegion,
  REGION_SUBDIVISION_CODES,
} from "@/lib/region/region";
import { PNW_ADMIN_CODES } from "@/lib/region/pnw";

describe("region subdivision codes", () => {
  it("are derived from the declared admin-code tuple, not restated beside it", () => {
    const expected = PNW_ADMIN_CODES.map((adminCode) => adminCode.split("-")[1]);
    expect(REGION_SUBDIVISION_CODES).toEqual(expected);
    expect(REGION_SUBDIVISION_CODES).toEqual(["WA", "OR", "ID"]);
  });

  it("agree with the parsed manifest, which is checked on read rather than at import", () => {
    // The value no longer comes from `getRegion()` -- a module-level manifest read is the trap
    // `federation.md` §1 names (STYLE-REVIEW-W4 S1) -- so this is the assertion that keeps the
    // derivation honest rather than merely convenient.
    expect(getRegion().adminCodes).toEqual([...PNW_ADMIN_CODES]);
  });

  it("refuse a manifest that binds a code the declared tuple does not carry", () => {
    // Adding to one side only is the edit W4 S2 describes: the runtime array grows to four while
    // every `RegionSubdivisionCode`-typed surface still promises three.
    expect(() => assertAdminCodesMatchDeclaredTuple([...PNW_ADMIN_CODES, "US-MT"])).toThrow(
      /may only be edited together/
    );
    expect(() => assertAdminCodesMatchDeclaredTuple(["US-OR", "US-WA", "US-ID"])).toThrow(
      /may only be edited together/
    );
    expect(() => assertAdminCodesMatchDeclaredTuple([...PNW_ADMIN_CODES])).not.toThrow();
  });

  it("are the ONE definition both former restatements now read", () => {
    // `PNW_STATE_CODES` (Drizzle side) and `PILOT_STATES` (reader side) were two hand-written
    // copies of the same three codes; a next region changing `admin_codes` used to leave the
    // manifest, the alias and the Parquet row schema disagreeing (STYLE-REVIEW-W2 S1/S2).
    expect(PNW_STATE_CODES).toBe(REGION_SUBDIVISION_CODES);
    expect(PILOT_STATES).toBe(REGION_SUBDIVISION_CODES);
  });
});
