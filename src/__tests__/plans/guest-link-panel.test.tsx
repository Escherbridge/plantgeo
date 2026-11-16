import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PlanWorkspace, type PlanWorkspaceModel } from "@/components/plans/PlanWorkspace";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/map/MapView", () => ({ default: () => <div>Map</div> }));
vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    plans: {
      create: { useMutation: () => ({ mutateAsync: vi.fn(), isPending: false }) },
      addItem: { useMutation: () => ({ mutateAsync: vi.fn(), isPending: false }) },
      updateItem: { useMutation: () => ({ mutateAsync: vi.fn(), isPending: false }) },
      removeItem: { useMutation: () => ({ mutateAsync: vi.fn(), isPending: false }) },
    },
  },
}));

const plan: PlanWorkspaceModel = {
  publicId: "plan_guest1234567890",
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

describe("guest edit link", () => {
  it("stays until the link is copied, then announces that and offers the account return", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <PlanWorkspace
        plan={plan}
        catalog={{ status: "empty" }}
        guestToken="guest-token-value"
        refCode={null}
        showStart={false}
      />,
    );

    expect(screen.getByRole("heading", { name: "Keep this link." })).toBeTruthy();
    expect(
      screen.getByText(
        "It's the only way to edit this plan without an account. Anyone with it can edit, so share it carefully.",
      ),
    ).toBeTruthy();
    const account = screen.getByRole("link", { name: "Create an account to keep this plan" });
    expect(account.getAttribute("href")).toBe(
      "/register?callbackUrl=%2Fplan%2Fplan_guest1234567890",
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy edit link" }));
    const copied = await screen.findByText("Link copied.");
    expect(copied.getAttribute("role")).toBe("status");
    expect(screen.queryByRole("heading", { name: "Keep this link." })).toBeNull();
    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/plan/plan_guest1234567890?token=guest-token-value`,
    );
  });
});
