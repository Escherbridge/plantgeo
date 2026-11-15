import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const dashboardWidget = vi.hoisted(() => vi.fn());
const plans = vi.hoisted(() => ({
  rows: [] as { publicId: string; title: string; updatedAt: string }[],
  isError: false,
  isPending: false,
}));
const sessionState = vi.hoisted(() => ({ role: null as string | null }));
vi.mock("@/components/dashboard/DashboardGrid", () => ({ DashboardGrid: dashboardWidget }));
vi.mock("@/components/auth/UserMenu", () => ({ UserMenu: () => <div>Account menu</div> }));
vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    plans: {
      list: {
        useQuery: () => ({ data: plans.rows, isError: plans.isError, isPending: plans.isPending }),
      },
    },
  },
}));
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
  afterEach(() => {
    plans.rows = [];
    plans.isError = false;
    plans.isPending = false;
    sessionState.role = null;
    cleanup();
  });

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
    expect(screen.getByRole("heading", { name: "Your plans" })).toBeTruthy();
    expect(
      screen.getByText(
        "You haven't started a plan yet. Plans open on a sample area in Ada County, Idaho for now.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Start a plan" }).getAttribute("href")).toBe("/plan/new");
  });

  it("lists the signed-in account's plans with an Open link", () => {
    plans.rows = [
      { publicId: "plan_abc", title: "North bed", updatedAt: "2026-11-01T00:00:00.000Z" },
    ];
    render(<DashboardPage />);
    expect(screen.getByText("North bed")).toBeTruthy();
    expect(screen.getByText("Last edited 2026-11-01")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open" }).getAttribute("href")).toBe("/plan/plan_abc");
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
