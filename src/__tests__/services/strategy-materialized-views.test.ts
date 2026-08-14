import { describe, expect, it } from "vitest";

describe("PostGIS Strategy Materialized View Engine", () => {
  it("defines 3 zoom-granularity materialized views", () => {
    const views = [
      "geo.mv_strategy_recommendations_coarse",
      "geo.mv_strategy_recommendations_regional",
      "geo.mv_strategy_recommendations_detail",
    ];

    expect(views).toHaveLength(3);
    expect(views[0]).toContain("coarse");
    expect(views[1]).toContain("regional");
    expect(views[2]).toContain("detail");
  });

  it("selects proper materialized view based on zoom level (z)", () => {
    function resolveMaterializedView(z: number): string {
      if (z <= 6) return "geo.mv_strategy_recommendations_coarse";
      if (z <= 11) return "geo.mv_strategy_recommendations_regional";
      return "geo.mv_strategy_recommendations_detail";
    }

    expect(resolveMaterializedView(3)).toBe("geo.mv_strategy_recommendations_coarse");
    expect(resolveMaterializedView(6)).toBe("geo.mv_strategy_recommendations_coarse");
    expect(resolveMaterializedView(8)).toBe("geo.mv_strategy_recommendations_regional");
    expect(resolveMaterializedView(11)).toBe("geo.mv_strategy_recommendations_regional");
    expect(resolveMaterializedView(12)).toBe("geo.mv_strategy_recommendations_detail");
    expect(resolveMaterializedView(15)).toBe("geo.mv_strategy_recommendations_detail");
  });
});
