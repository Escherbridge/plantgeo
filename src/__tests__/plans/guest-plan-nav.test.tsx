import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TopBar from "@/components/layout/TopBar";
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
  });
});
