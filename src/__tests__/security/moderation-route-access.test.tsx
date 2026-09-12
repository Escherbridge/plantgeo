import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

const mocks = vi.hoisted(() => ({ getServerSession: vi.fn() }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: mocks.getServerSession }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("@/components/panels/ContributionQueue", () => ({ ContributionQueue: () => <div data-testid="contribution-queue-stub" /> }));
import ModerationPage from "@/app/moderation/page";

beforeEach(() => vi.clearAllMocks());

describe("moderation route access", () => {
  it("distinguishes an authentication service failure from denied access", async () => {
    mocks.getServerSession.mockRejectedValue(new Error("session service unavailable"));
    render(await ModerationPage());
    expect(screen.getByRole("alert").textContent).toContain("sign-in service is unavailable");
    expect(screen.queryByTestId("contribution-queue-stub")).toBeNull();
  });
  it("explains authentication without mounting a queue for a signed-out visitor", async () => {
    mocks.getServerSession.mockResolvedValue(null);
    render(await ModerationPage());
    expect(screen.getByText("Sign in to review community recommendations.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toContain("callbackUrl=%2Fmoderation");
    expect(screen.queryByTestId("contribution-queue-stub")).toBeNull();
  });

  it.each(["contributor", "viewer"])("explains denied %s access without mounting a queue", async (platformRole) => {
    mocks.getServerSession.mockResolvedValue({ user: { platformRole } });
    render(await ModerationPage());
    expect(screen.getByText("Only experts and administrators can review community recommendations.")).toBeTruthy();
    expect(screen.queryByTestId("contribution-queue-stub")).toBeNull();
  });

  it.each(["expert", "admin"])("mounts exactly one canonical queue for %s", async (platformRole) => {
    mocks.getServerSession.mockResolvedValue({ user: { platformRole } });
    render(await ModerationPage());
    expect(screen.getAllByTestId("contribution-queue-stub")).toHaveLength(1);
    expect(screen.queryByText(/Lifecycle/)).toBeNull();
  });
});
