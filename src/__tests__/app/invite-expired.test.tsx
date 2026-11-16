import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const session = vi.hoisted(() => ({ status: "unauthenticated" as "unauthenticated" | "authenticated" }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ token: "used-token" }),
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next-auth/react", () => ({
  useSession: () =>
    session.status === "authenticated"
      ? { data: { user: { email: "owner@example.com" } }, status: "authenticated" }
      : { data: null, status: "unauthenticated" },
  signOut: vi.fn(),
}));
vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    useUtils: () => ({ teams: { listMyTeams: { invalidate: vi.fn() } } }),
    teams: {
      previewInvitation: {
        useQuery: () => ({ isLoading: false, isError: false, data: { valid: false }, error: null }),
      },
      acceptInvitation: {
        useMutation: () => ({ mutateAsync: vi.fn(), isPending: false, error: null }),
      },
      setActiveTeam: {
        useMutation: () => ({ mutateAsync: vi.fn(), isPending: false, error: null }),
      },
    },
  },
}));
vi.mock("@/components/ui/toast", () => ({ toast: { success: vi.fn() } }));

import InviteTokenPage from "@/app/invite/[token]/page";

describe("expired invitation", () => {
  it("tells a signed-out visitor the link has expired and opens the map", () => {
    session.status = "unauthenticated";
    render(<InviteTokenPage />);
    expect(screen.getByRole("heading", { name: "This invitation has expired." })).toBeTruthy();
    expect(
      screen.getByText(
        "It may have been used already, withdrawn, or run past its seven days. Ask the person who invited you for a new link.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open the map" }).getAttribute("href")).toBe("/");
    expect(screen.queryByRole("link", { name: "Go to dashboard" })).toBeNull();
  });

  it("sends a signed-in visitor to the dashboard", () => {
    session.status = "authenticated";
    render(<InviteTokenPage />);
    expect(screen.getByRole("link", { name: "Go to dashboard" }).getAttribute("href")).toBe(
      "/dashboard",
    );
  });
});
