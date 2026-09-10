import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  loading: false,
  failed: false,
  refetch: vi.fn(),
  replace: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: state.replace }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/trpc/client", () => ({
  trpc: { teams: { listMyTeams: { useQuery: () => ({
    data: [], isLoading: state.loading, isError: state.failed,
    error: { message: "Organization service unavailable" }, refetch: state.refetch,
  }) } } },
}));
vi.mock("@/components/onboarding/CreateOrganizationForm", () => ({
  CreateOrganizationForm: () => <div>Create form</div>,
}));
vi.mock("@/components/onboarding/JoinOrganizationForm", () => ({
  JoinOrganizationForm: () => <div>Join form</div>,
}));

import OnboardingPage from "@/app/onboarding/page";

describe("optional organization onboarding", () => {
  beforeEach(() => {
    state.loading = false;
    state.failed = false;
  });
  afterEach(cleanup);

  it.each(["loading", "failed"] as const)("keeps the individual exit available when organizations are %s", (phase) => {
    state[phase] = true;
    render(<OnboardingPage />);
    expect(screen.getByRole("link", { name: "Continue individually" }).getAttribute("href")).toBe("/dashboard");
  });

  it.each(["Create", "Join"])("keeps the individual exit after opening the %s form", (action) => {
    render(<OnboardingPage />);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(`${action} an organization`) }));
    expect(screen.getByText(`${action} form`).textContent).toBe(`${action} form`);
    expect(screen.getByRole("link", { name: "Continue individually" }).getAttribute("href")).toBe("/dashboard");
  });
});
