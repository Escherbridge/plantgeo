import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";

/**
 * ModerationPage is a server component: called directly as an async function,
 * not rendered through a Next request. ContributionQueue and ModerationPanel are
 * stubbed so this test exercises only the route's role gate (redirect-before-render)
 * and that both moderation surfaces mount behind it -- each has its own test file.
 */
const mocks = vi.hoisted(() => ({
  getServerSession: vi.fn(),
  redirect: vi.fn(),
}));

vi.mock("@/lib/server/auth", () => ({ getServerSession: mocks.getServerSession }));
vi.mock("next/navigation", () => ({ redirect: mocks.redirect }));
vi.mock("@/components/panels/ContributionQueue", () => ({
  ContributionQueue: () => <div data-testid="contribution-queue-stub" />,
}));
vi.mock("@/components/panels/ModerationPanel", () => ({
  ModerationPanel: () => <div data-testid="lifecycle-panel-stub" />,
}));

import ModerationPage from "@/app/moderation/page";

function sessionFor(platformRole: string | undefined) {
  return platformRole === undefined ? null : { user: { platformRole } };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("moderation route access", () => {
  it("redirects home and renders nothing for a signed-out visitor", async () => {
    mocks.getServerSession.mockResolvedValue(null);

    const result = await ModerationPage();

    expect(mocks.redirect).toHaveBeenCalledWith("/");
    expect(result).toBeNull();
  });

  it("redirects home and renders nothing for a contributor", async () => {
    mocks.getServerSession.mockResolvedValue(sessionFor("contributor"));

    const result = await ModerationPage();

    expect(mocks.redirect).toHaveBeenCalledWith("/");
    expect(result).toBeNull();
  });

  it("mounts the review queue and lifecycle panel for an expert without redirecting", async () => {
    mocks.getServerSession.mockResolvedValue(sessionFor("expert"));

    const result = await ModerationPage();

    expect(mocks.redirect).not.toHaveBeenCalled();
    render(result as ReactElement);
    expect(screen.getByTestId("contribution-queue-stub")).toBeTruthy();
    expect(screen.getByTestId("lifecycle-panel-stub")).toBeTruthy();
  });

  it("mounts the review queue and lifecycle panel for an admin without redirecting", async () => {
    mocks.getServerSession.mockResolvedValue(sessionFor("admin"));

    const result = await ModerationPage();

    expect(mocks.redirect).not.toHaveBeenCalled();
    render(result as ReactElement);
    expect(screen.getByTestId("contribution-queue-stub")).toBeTruthy();
    expect(screen.getByTestId("lifecycle-panel-stub")).toBeTruthy();
  });
});
