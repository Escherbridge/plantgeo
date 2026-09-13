import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * Phase 2 of `public_strategy_requests_20260913`: the ONE modal opens for both
 * kinds, and a reader can tell which one they opened.
 *
 * The distinction is asserted as a separate badge rather than as text folded
 * into Type/Category on purpose: those two fields say what kind of work is
 * involved, which an ask and a proposal answer identically. What differs is
 * whether anyone has committed to doing it, and that is what the badge says.
 */

vi.mock("@/components/intervention/InterventionLikeButton", () => ({
  InterventionLikeButton: ({ featureId }: { featureId: string }) => (
    <div data-testid="like-button-stub" data-feature-id={featureId} />
  ),
}));
vi.mock("@/components/intervention/InterventionCommentThread", () => ({
  InterventionCommentThread: ({ featureId }: { featureId: string }) => (
    <div data-testid="comment-thread-stub" data-feature-id={featureId} />
  ),
}));

import {
  InterventionDetailCard,
  type InterventionDetailCardProps,
} from "@/components/map/InterventionDetailModal";
import { INTERVENTION_REQUEST_LABEL } from "@/lib/map/layers";
import type { InterventionDetailRecord } from "@/lib/map/intervention-detail";

const POINT: GeoJSON.Geometry = { type: "Point", coordinates: [-120.1, 46.1] };

const REQUEST_RECORD: InterventionDetailRecord = {
  id: "77777777-7777-4777-8777-777777777777",
  name: "Swale the north slope",
  type: "water_harvesting",
  category: "land",
  kind: "request",
  status: "published",
  description: "This hillside sheets water every spring.",
  geometry: POINT,
  submittedByUserId: "22222222-2222-4222-8222-222222222222",
  submittedByTeamId: null,
  createdAt: new Date("2026-09-13T00:00:00.000Z"),
  updatedAt: new Date("2026-09-13T00:00:00.000Z"),
  reviewNote: null,
  hasFullGeometry: true,
};

function renderCard(overrides: Partial<InterventionDetailCardProps> = {}) {
  const props: InterventionDetailCardProps = {
    record: REQUEST_RECORD,
    isLoading: false,
    isError: false,
    isExpanded: false,
    onToggleExpanded: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  return render(<InterventionDetailCard {...props} />);
}

afterEach(cleanup);

describe("the detail modal on a request feature (FR-2)", () => {
  it("labels it a community request", () => {
    renderCard();

    const badge = screen.getByTestId("intervention-kind-badge");
    expect(badge.textContent).toBe(INTERVENTION_REQUEST_LABEL);
    expect(badge.getAttribute("data-kind")).toBe("request");
    expect(INTERVENTION_REQUEST_LABEL.toLowerCase()).toContain("request");
  });

  it("says request in its accessible name, not intervention", () => {
    renderCard();

    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-label")).toBe("Strategy request details");
    expect(dialog.getAttribute("data-kind")).toBe("request");
  });

  it("still renders the shared fields -- it is one modal, not two", () => {
    renderCard();

    expect(screen.getByText("Swale the north slope")).toBeTruthy();
    expect(screen.getByText("water_harvesting")).toBeTruthy();
    expect(screen.getByText("Land intervention")).toBeTruthy();
    expect(screen.getByText("This hillside sheets water every spring.")).toBeTruthy();
    expect(screen.getByTestId("intervention-geometry-preview")).toBeTruthy();
    expect(screen.getByTestId("intervention-status-pill").getAttribute("data-status")).toBe(
      "published"
    );
  });

  it("shows no request badge for an intervention, including a `kind`-less legacy row", () => {
    renderCard({
      record: { ...REQUEST_RECORD, kind: "intervention" },
    });
    expect(screen.queryByTestId("intervention-kind-badge")).toBeNull();
    expect(screen.getByRole("dialog").getAttribute("aria-label")).toBe(
      "Intervention details"
    );
    cleanup();

    const legacy = { ...REQUEST_RECORD };
    delete (legacy as { kind?: unknown }).kind;
    renderCard({ record: legacy });
    expect(screen.queryByTestId("intervention-kind-badge")).toBeNull();
    expect(screen.getByRole("dialog").getAttribute("data-kind")).toBe("intervention");
  });
});
