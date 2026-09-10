import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const dashboardWidget = vi.hoisted(() => vi.fn());
vi.mock("@/components/dashboard/DashboardGrid", () => ({ DashboardGrid: dashboardWidget }));
vi.mock("@/components/auth/UserMenu", () => ({ UserMenu: () => <div>Account menu</div> }));
import DashboardPage from "@/app/dashboard/page";

describe("Dashboard published entrypoints", () => {
  it("keeps working destinations without mounting placeholder analytics", () => {
    render(<DashboardPage />);
    expect(screen.getByRole("link", { name: /Explore the map/ }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("link", { name: /Organization/ }).getAttribute("href")).toBe("/dashboard/org");
    expect(screen.getByRole("link", { name: /AI Conversations/ }).getAttribute("href")).toBe("/dashboard/conversations");
    expect(dashboardWidget).not.toHaveBeenCalled();
    expect(screen.queryByText("Fleet Overview")).toBeNull();
    expect(screen.queryByText("Map Preview")).toBeNull();
    expect(screen.queryByText("Operational Metrics")).toBeNull();
  });
});
