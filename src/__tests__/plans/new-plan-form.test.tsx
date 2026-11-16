import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NewPlanForm } from "@/app/plan/new/NewPlanForm";
import { GUEST_PLAN_STORAGE_KEY, rememberGuestPlan } from "@/lib/plans/guest-plan-links";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  mutateAsync: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    plans: {
      create: { useMutation: () => ({ mutateAsync: mocks.mutateAsync, isPending: false }) },
    },
  },
}));

const saved = {
  publicId: "plan_guest12345678901",
  token: "guest-token-value",
  title: "North field",
};

describe("new plan form", () => {
  beforeEach(() => {
    mocks.push.mockReset();
    mocks.mutateAsync.mockReset();
    localStorage.removeItem(GUEST_PLAN_STORAGE_KEY);
  });

  it("shows the full edit link before leaving, and does not redirect a guest", async () => {
    mocks.mutateAsync.mockResolvedValue({
      publicId: "plan_newguest12345678",
      guestToken: "fresh-guest-token",
    });
    render(<NewPlanForm />);
    fireEvent.click(screen.getByRole("button", { name: "Start your plan" }));

    const fullLink = `${window.location.origin}/plan/plan_newguest12345678?token=fresh-guest-token`;
    expect(await screen.findByRole("heading", { name: "Keep this link." })).toBeTruthy();
    expect(screen.getByRole("link", { name: fullLink }).getAttribute("href")).toBe(
      "/plan/plan_newguest12345678?token=fresh-guest-token"
    );
    expect(screen.getByRole("button", { name: "Copy edit link" })).toBeTruthy();
    expect(mocks.push).not.toHaveBeenCalled();
    expect(localStorage.getItem(GUEST_PLAN_STORAGE_KEY)).toContain("fresh-guest-token");
  });

  it("still opens an account plan immediately", async () => {
    mocks.mutateAsync.mockResolvedValue({ publicId: "plan_signedin12345678", guestToken: null });
    render(<NewPlanForm />);
    fireEvent.click(screen.getByRole("button", { name: "Start your plan" }));
    await waitFor(() => {
      expect(mocks.push).toHaveBeenCalledWith("/plan/plan_signedin12345678");
    });
    expect(screen.queryByRole("heading", { name: "Keep this link." })).toBeNull();
  });

  it("shows the saved edit link when another plan cannot be started", async () => {
    rememberGuestPlan(localStorage, saved);
    mocks.mutateAsync.mockRejectedValue(new Error("blocked"));
    render(<NewPlanForm />);
    fireEvent.click(screen.getByRole("button", { name: "Start your plan" }));

    expect(await screen.findByRole("status")).toHaveProperty(
      "textContent",
      "The plan could not be started. Try again in a moment."
    );
    expect(screen.getByRole("link", { name: "Your guest plan" }).getAttribute("href")).toBe(
      "/plan/plan_guest12345678901?token=guest-token-value"
    );
  });
});
