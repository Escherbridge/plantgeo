import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";

/**
 * ModerationPage is a server component: called directly as an async function,
 * not rendered through a Next request. ContributionQueue is stubbed so this test
 * exercises only the route's role gate (redirect-before-render), which is the
 * thing this file owns -- the queue's own behavior has its own test file.
 */
const mocks = vi.hoisted(() => ({
  getServerSession: vi.fn(),
  redirect: vi.fn(),
}));

vi.mock("@/lib/server/auth", () => ({ getServerSession: mocks.getServerSession }));
vi.mock("next/navigation", () => ({ redirect: mocks.redirect }));
vi.mock("@/components/panels/ModerationPanel", () => ({
  ModerationPanel: () => <div data-testid="contribution-queue-stub" />,
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

  it("mounts the queue for an expert without redirecting", async () => {
    mocks.getServerSession.mockResolvedValue(sessionFor("expert"));

    const result = await ModerationPage();

    expect(mocks.redirect).not.toHaveBeenCalled();
    render(result as ReactElement);
    expect(screen.getByTestId("contribution-queue-stub")).toBeTruthy();
  });

  it("mounts the queue for an admin without redirecting", async () => {
    mocks.getServerSession.mockResolvedValue(sessionFor("admin"));

    const result = await ModerationPage();

    expect(mocks.redirect).not.toHaveBeenCalled();
    render(result as ReactElement);
    expect(screen.getByTestId("contribution-queue-stub")).toBeTruthy();
  });
});
