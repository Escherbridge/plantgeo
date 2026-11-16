import { act, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TopBar from "@/components/layout/TopBar";
import { HomeGuestPlans, YourGuestPlans } from "@/components/plans/YourGuestPlans";
import { GUEST_PLAN_STORAGE_KEY, rememberGuestPlan } from "@/lib/plans/guest-plan-links";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: null, status: "unauthenticated" }),
  signOut: vi.fn(),
}));

describe("guest plan in the nav", () => {
  beforeEach(() => {
    localStorage.removeItem(GUEST_PLAN_STORAGE_KEY);
  });

  it("offers Your guest plan when this browser has a saved edit link", () => {
    rememberGuestPlan(localStorage, {
      publicId: "plan_guest12345678901",
      token: "guest-token-value",
      title: "North field",
    });
    render(<TopBar />);
    const link = screen.getByRole("link", { name: "Your guest plan" });
    expect(link.getAttribute("href")).toBe("/plan/plan_guest12345678901?token=guest-token-value");
  });

  it("leaves the nav alone when no guest plan is saved", () => {
    render(<TopBar />);
    expect(screen.queryByRole("link", { name: "Your guest plan" })).toBeNull();
    expect(screen.queryByText("Your guest plans")).toBeNull();
  });

  it("lists every saved plan by name when there is more than one", () => {
    rememberGuestPlan(localStorage, {
      publicId: "plan_guest12345678901",
      token: "guest-token-value",
      title: "North field",
    });
    rememberGuestPlan(localStorage, {
      publicId: "plan_newer12345678901",
      token: "newer-token-value",
      title: "Creek bed",
    });
    render(<TopBar />);
    const primary = screen.getByRole("navigation", { name: "Primary" });
    expect(within(primary).getByText("Your guest plans")).toBeTruthy();
    expect(within(primary).queryByRole("link", { name: "Your guest plan" })).toBeNull();
    expect(within(primary).getByRole("link", { name: "Creek bed" }).getAttribute("href")).toBe(
      "/plan/plan_newer12345678901?token=newer-token-value"
    );
    expect(within(primary).getByRole("link", { name: "North field" }).getAttribute("href")).toBe(
      "/plan/plan_guest12345678901?token=guest-token-value"
    );
  });
});

describe("guest plans on the home page and the new-plan page", () => {
  beforeEach(() => {
    localStorage.removeItem(GUEST_PLAN_STORAGE_KEY);
  });

  it("shows one plan under the singular label and every plan by name after that", () => {
    const first = {
      publicId: "plan_guest12345678901",
      token: "guest-token-value",
      title: "North field",
    };
    rememberGuestPlan(localStorage, first);
    const { rerender } = render(<YourGuestPlans />);
    expect(screen.getByRole("link", { name: "Your guest plan" }).getAttribute("href")).toBe(
      "/plan/plan_guest12345678901?token=guest-token-value"
    );

    act(() => {
      rememberGuestPlan(localStorage, {
        publicId: "plan_newer12345678901",
        token: "newer-token-value",
        title: "Creek bed",
      });
    });
    rerender(<YourGuestPlans />);
    expect(screen.getByText("Your guest plans")).toBeTruthy();
    expect(screen.getByRole("link", { name: "North field" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Creek bed" })).toBeTruthy();

    rerender(<HomeGuestPlans />);
    expect(screen.getByText("Your guest plans")).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "North field" }).length).toBeGreaterThan(0);
  });
});
