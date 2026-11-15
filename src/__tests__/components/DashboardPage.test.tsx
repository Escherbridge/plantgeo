import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const dashboardWidget = vi.hoisted(() => vi.fn());
const sessionState = vi.hoisted(() => ({ role: null as string | null }));
vi.mock("@/components/dashboard/DashboardGrid", () => ({ DashboardGrid: dashboardWidget }));
vi.mock("@/components/auth/UserMenu", () => ({ UserMenu: () => <div>Account menu</div> }));
vi.mock("next-auth/react", () => ({
  useSession: () => ({
    data: sessionState.role
      ? { user: { platformRole: sessionState.role }, expires: "2099-01-01" }
      : null,
    status: sessionState.role ? "authenticated" : "unauthenticated",
  }),
}));
import DashboardPage from "@/app/dashboard/page";

describe("Dashboard published entrypoints", () => {
  it("keeps working destinations without mounting placeholder analytics", () => {
    sessionState.role = null;
    render(<DashboardPage />);
    expect(screen.getByRole("link", { name: /Explore the map/ }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("link", { name: /Organization/ }).getAttribute("href")).toBe("/dashboard/org");
    expect(screen.getByRole("link", { name: /AI Conversations/ }).getAttribute("href")).toBe("/dashboard/conversations");
    expect(screen.getByRole("link", { name: /Analytics/ }).getAttribute("href")).toBe("/dashboard/analytics");
    expect(screen.queryByRole("link", { name: /Your invite link/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Affiliate applications/ })).toBeNull();
    expect(dashboardWidget).not.toHaveBeenCalled();
    expect(screen.queryByText("Fleet Overview")).toBeNull();
    expect(screen.queryByText("Map Preview")).toBeNull();
    expect(screen.queryByText("Operational Metrics")).toBeNull();
  });

  it("shows the invite link to an affiliate and the review list to an admin", () => {
    sessionState.role = "affiliate";
    const { unmount } = render(<DashboardPage />);
    expect(screen.getByRole("link", { name: /Your invite link/ }).getAttribute("href")).toBe(
      "/dashboard/affiliate"
    );
    expect(screen.queryByRole("link", { name: /Affiliate applications/ })).toBeNull();
    unmount();

    sessionState.role = "admin";
    render(<DashboardPage />);
    expect(screen.getByRole("link", { name: /Affiliate applications/ }).getAttribute("href")).toBe(
      "/dashboard/admin/affiliates"
    );
    expect(screen.queryByRole("link", { name: /Your invite link/ })).toBeNull();
  });
});
