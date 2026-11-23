import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DocsPage from "@/app/docs/page";

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
});
