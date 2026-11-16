import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PlanWorkspace, type PlanWorkspaceModel } from "@/components/plans/PlanWorkspace";
import { GUEST_PLAN_STORAGE_KEY } from "@/lib/plans/guest-plan-links";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  mutateAsync: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/components/map/MapView", () => ({ default: () => <div>Map</div> }));
vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    plans: {
      create: { useMutation: () => ({ mutateAsync: mocks.mutateAsync, isPending: false }) },
      addItem: { useMutation: () => ({ mutateAsync: vi.fn(), isPending: false }) },
      updateItem: { useMutation: () => ({ mutateAsync: vi.fn(), isPending: false }) },
      removeItem: { useMutation: () => ({ mutateAsync: vi.fn(), isPending: false }) },
    },
  },
}));

const plan: PlanWorkspaceModel = {
  publicId: "plan_guest12345678901",
  title: "Site plan",
  description: null,
  isSample: false,
  sampleLabel: null,
  canEdit: true,
  savedToAccount: false,
  missing: false,
  items: [],
  camera: null,
};

const fullLink = `${window.location.origin}/plan/plan_guest12345678901?token=guest-token-value`;

describe("guest edit link", () => {
  beforeEach(() => {
    mocks.push.mockReset();
    mocks.mutateAsync.mockReset();
    localStorage.removeItem(GUEST_PLAN_STORAGE_KEY);
  });

  it("shows the full edit link, and copying it leaves the link on the page", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <PlanWorkspace
        plan={plan}
        catalog={{ status: "empty" }}
        guestToken="guest-token-value"
        refCode={null}
        showStart={false}
      />
    );

    expect(screen.getByRole("heading", { name: "Keep this link." })).toBeTruthy();
    expect(
      screen.getByText(
        "It's the only way to edit this plan without an account. Anyone with it can edit, so share it carefully."
      )
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: fullLink }).getAttribute("href")).toBe(
      "/plan/plan_guest12345678901?token=guest-token-value"
    );
    const account = screen.getByRole("link", { name: "Create an account to keep this plan" });
    expect(account.getAttribute("href")).toBe("/register?callbackUrl=%2Fplan%2Fplan_guest12345678901");

    fireEvent.click(screen.getByRole("button", { name: "Copy edit link" }));
    const copied = await screen.findByText("Link copied.");
    expect(copied.getAttribute("role")).toBe("status");
    expect(screen.getByRole("heading", { name: "Keep this link." })).toBeTruthy();
    expect(screen.getByRole("link", { name: fullLink })).toBeTruthy();
    expect(writeText).toHaveBeenCalledWith(fullLink);
  });

  it("keeps a guest on this page until the edit link is on screen", async () => {
    mocks.mutateAsync.mockResolvedValue({
      publicId: "plan_newguest12345678",
      guestToken: "fresh-guest-token",
    });
    render(
      <PlanWorkspace
        plan={{ ...plan, publicId: "sample-ada-county", isSample: true, canEdit: false }}
        catalog={{ status: "empty" }}
        guestToken={null}
        refCode={null}
        showStart
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Start your plan" }));
    const created = `${window.location.origin}/plan/plan_newguest12345678?token=fresh-guest-token`;
    expect(await screen.findByRole("link", { name: created })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Keep this link." })).toBeTruthy();
    expect(mocks.push).not.toHaveBeenCalled();
  });
});
