import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DocsPage from "@/app/docs/page";

const PARAMETER_SECTIONS = ["Features", "Geocoding"] as const;

/** The same ring classes CodeBlock shares via SCROLL_REGION_FOCUS. */
const SCROLL_REGION_FOCUS = [
  "focus:outline-none",
  "focus:ring-2",
  "focus:ring-[hsl(var(--ring))]",
] as const;

describe("/docs parameter table", () => {
  it("labels a required parameter Required instead of Yes", () => {
    const { container } = render(<DocsPage />);
    const required = [...container.querySelectorAll("td")].filter(
      (cell) => cell.textContent === "Required",
    );
    expect(required).toHaveLength(1);
    expect(required[0]?.querySelector("span")?.className).toContain("text-[#f87171]");
    expect([...container.querySelectorAll("td")].some((cell) => cell.textContent === "Yes")).toBe(
      false,
    );
    expect(screen.getByLabelText("Endpoint").tagName).toBe("SELECT");
  });

  it("makes every parameter table a labelled focusable region", () => {
    render(<DocsPage />);

    const regions = PARAMETER_SECTIONS.map((section) =>
      screen.getByRole("region", { name: `Parameters: ${section}` }),
    );

    expect(screen.getAllByRole("region", { name: /^Parameters: / })).toHaveLength(
      PARAMETER_SECTIONS.length,
    );

    for (const region of regions) {
      expect(region.tagName).toBe("DIV");
      expect(region.tabIndex).toBe(0);
      expect(region.className).toContain("overflow-x-auto");
      for (const token of SCROLL_REGION_FOCUS) {
        expect(region.className).toContain(token);
      }
      expect(region.querySelector("table")).not.toBeNull();
    }
  });

  it("lets the Description column wrap inside a narrow viewport", () => {
    render(<DocsPage />);
    const region = screen.getByRole("region", { name: "Parameters: Features" });
    const table = region.querySelector("table");
    expect(table?.className).toContain("table-fixed");
    expect(table?.className).toContain("sm:table-auto");
    expect(table?.className).toContain("break-words");

    const descriptions = [...region.querySelectorAll("tbody tr td:last-child")];
    expect(descriptions.length).toBeGreaterThan(0);
    for (const cell of descriptions) {
      expect(cell.className).toContain("break-words");
    }
  });
});
