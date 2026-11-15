import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AffiliateInvitePanel } from "@/app/dashboard/affiliate/AffiliateInvitePanel";
import { AffiliateReviewList } from "@/app/dashboard/admin/affiliates/AffiliateReviewList";
import { PlanInviteLine } from "@/app/plan/new/PlanInviteLine";
import type { AffiliateApplicationListItem } from "@/lib/affiliate/application";

const INVITE_URL = "https://plantgeo.aevani.com/plan/new?ref=ada-planner-ab12cd";
const APPLICATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

const pending: AffiliateApplicationListItem = {
  id: APPLICATION_ID,
  name: "Ada Planner",
  email: "ada@example.com",
  organization: "Ada Gardens",
  role: "designer",
  website: "https://example.org/garden",
  message: "I teach site planning.",
  status: "pending",
  code: null,
};

function assertNoPayoutFigure(text: string) {
  expect(text).not.toMatch(/\d+%/);
  expect(text).not.toMatch(/\$\s?\d/);
  expect(text).not.toMatch(/\b\d{4}-\d{2}-\d{2}\b/);
  expect(text).not.toMatch(/commission/i);
  expect(text).not.toMatch(/payout date/i);
}

describe("invited planner surfaces", () => {
  it("shows the invite line for a known affiliate and nothing for an unknown code", () => {
    const { rerender } = render(
      <PlanInviteLine line="Ada Planner invited you to plan a site. They can see that you started a plan, but not what's in it." />
    );
    expect(screen.getByText(/Ada Planner invited you to plan a site/)).toBeTruthy();
    expect(document.body.textContent).toMatch(/but not what's in it/);

    rerender(<PlanInviteLine line={null} />);
    expect(document.body.textContent ?? "").not.toMatch(/invited you/);
  });

  it("shows the invite link, copy button, and plans-started count", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    render(<AffiliateInvitePanel inviteUrl={INVITE_URL} plansStarted={1} />);
    const text = document.body.textContent ?? "";
    expect(screen.getByRole("heading", { name: "Your invite link" })).toBeTruthy();
    expect(text).toMatch(
      /Send this link to a landowner, a grower, a school, or a homestead\. When they start a plan from it, it is counted on this page\. A referral share has not been set, so this page does not show a rate or a payout\./
    );
    expect(screen.getByRole("link", { name: INVITE_URL }).getAttribute("href")).toBe(INVITE_URL);
    expect(text).toMatch(/Plans started\s*1/);
    assertNoPayoutFigure(text);

    fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(INVITE_URL));
    expect(screen.getByRole("status").textContent).toBe("Link copied.");
  });

  it("approves or declines a pending application without showing a payout or a date", () => {
    const onApprove = vi.fn();
    const onDecline = vi.fn();
    const { rerender } = render(
      <AffiliateReviewList
        applications={[pending]}
        pendingId={null}
        notice={null}
        onApprove={onApprove}
        onDecline={onDecline}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Approve Ada Planner" }));
    expect(onApprove).toHaveBeenCalledWith(APPLICATION_ID);
    fireEvent.click(screen.getByRole("button", { name: "Decline Ada Planner" }));
    expect(onDecline).toHaveBeenCalledWith(APPLICATION_ID);
    assertNoPayoutFigure(document.body.textContent ?? "");

    rerender(
      <AffiliateReviewList
        applications={[{ ...pending, status: "approved", code: "ada-planner-ab12cd" }]}
        pendingId={null}
        notice={null}
        onApprove={onApprove}
        onDecline={onDecline}
      />
    );
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
    expect(document.body.textContent).toMatch(/ada-planner-ab12cd/);
    expect(document.body.textContent).toMatch(/Approved/);
    assertNoPayoutFigure(document.body.textContent ?? "");
  });
});
