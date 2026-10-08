import { describe, expect, it, vi } from "vitest";

/**
 * The agri <-> web contract for `distribution_at_point`, from real agri outputs: the fixture is
 * written and checked by agri's `test_the_web_distribution_contract_fixture_is_what_the_tool_returns`
 * (services/agri-data-service/tests/test_agent_window_distribution.py). Every case must parse, and
 * each must show -- or hide -- the line the map would, and be cached for the right lifetime.
 */
vi.mock("@/lib/server/db", () => ({ db: {} }));

import contractFixture from "./agri-distribution-contract.fixture.json";
import {
  describeLaneDistribution,
  distributionAtPointResultSchema,
  selectDistributionLane,
} from "@/lib/layer-window-distribution";
import {
  distributionCacheSeconds,
  SETTLED_CACHE_SECONDS,
  TRANSIENT_CACHE_SECONDS,
} from "@/lib/server/services/layer-window-distribution";

const CASES = contractFixture as Record<string, unknown>;

/** Per agri case: the one line the tooltip shows (null = nothing) and how long the answer is cached. */
const EXPECTED: Record<string, { line: string | null; cacheSeconds: number }> = {
  published: {
    line: "30 d: median 4.00 degC (p10 2.40 – p90 5.60) · 2 of 30 days",
    cacheSeconds: SETTLED_CACHE_SECONDS,
  },
  published_truncated: {
    line: "30 d (latest 25 d read): median 7.00 degC (p10 6.20 – p90 7.80) · 2 of 25 days",
    cacheSeconds: SETTLED_CACHE_SECONDS,
  },
  published_nearest_cell: {
    line: "30 d: median 0.50 unitless (p10 0.30 – p90 0.70) · 2 of 30 days · nearest cell 17.4 km",
    cacheSeconds: SETTLED_CACHE_SECONDS,
  },
  published_fire_zero_at_point: {
    line: "30 d: median 0.00 count (p10 0.00 – p90 0.00) · 2 of 30 days",
    cacheSeconds: SETTLED_CACHE_SECONDS,
  },
  no_data_in_window: { line: "30 d: no data · 0 of 30 days", cacheSeconds: SETTLED_CACHE_SECONDS },
  static_not_applicable: { line: null, cacheSeconds: SETTLED_CACHE_SECONDS },
  refused_release_lane: { line: null, cacheSeconds: SETTLED_CACHE_SECONDS },
  refused_at_capacity: { line: null, cacheSeconds: TRANSIENT_CACHE_SECONDS },
  whole_call_refusal: { line: null, cacheSeconds: SETTLED_CACHE_SECONDS },
  whole_call_unknown_signal: { line: null, cacheSeconds: SETTLED_CACHE_SECONDS },
};

describe("the agri distribution contract fixture", () => {
  it("names exactly the cases this test expects", () => {
    expect(Object.keys(CASES).sort()).toEqual(Object.keys(EXPECTED).sort());
  });

  it.each(Object.entries(EXPECTED))("parses %s and shows what the map would", (name, expected) => {
    const result = distributionAtPointResultSchema.parse(CASES[name]);
    const lane = selectDistributionLane(result, null);
    expect(lane === null ? null : describeLaneDistribution(lane)).toBe(expected.line);
    expect(distributionCacheSeconds(result)).toBe(expected.cacheSeconds);
  });
});
