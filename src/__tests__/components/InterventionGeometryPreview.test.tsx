import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { InterventionGeometryPreview } from "@/components/panels/InterventionGeometryPreview";

describe("submitted geometry review preview", () => {
  it("shows a pending polygon with a hole without relying on published tiles", () => {
    render(<InterventionGeometryPreview geometry={{ type: "Polygon", coordinates: [
      [[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]],
      [[1, 1], [1, 2], [2, 2], [2, 1], [1, 1]],
    ] }} />);
    const svg = screen.getByRole("img", { name: "Polygon submitted geometry preview" });
    expect(svg.querySelector("path")?.getAttribute("fill-rule")).toBe("evenodd");
    expect(svg.querySelector("path")?.getAttribute("d")?.match(/M/g)).toHaveLength(2);
    expect(screen.getByText(/10 coordinate positions/)).toBeTruthy();
    expect(screen.getByText("Inspect submitted GeoJSON")).toBeTruthy();
    expect(screen.getByText(/public map only shows published sites/)).toBeTruthy();
  });

  it("marks malformed coordinates as requiring correction instead of drawing a misleading outline", () => {
    render(<InterventionGeometryPreview geometry={{ type: "Polygon", coordinates: [[[181, 0], [0, 0]]] }} />);
    expect(screen.getByText(/cannot be previewed/)).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });
});
