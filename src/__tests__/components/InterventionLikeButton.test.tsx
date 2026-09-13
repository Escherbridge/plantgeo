import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The shared like control (Phase 5 / FR-4), mounted on BOTH the map detail modal
 * and `/feed`, so it is tested once, here, against stubbed tRPC hooks.
 *
 * The load-bearing properties: the count and the viewer's own state come from
 * `getLikeState`; a click calls `toggleLike` and the DISPLAY moves to the value
 * the server returned without the component remounting; and a signed-out viewer
 * gets `/feed`'s sign-in affordance rather than an unhandled UNAUTHORIZED.
 */

const mocks = vi.hoisted(() => ({
  session: { data: null as unknown, status: "authenticated" as string },
  likeStateQuery: vi.fn(),
  toggleMutate: vi.fn(),
}));

vi.mock("next-auth/react", () => ({
  useSession: () => mocks.session,
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    interventionSocial: {
      getLikeState: { useQuery: mocks.likeStateQuery },
      toggleLike: {
        useMutation: (opts?: {
          onSuccess?: (data: unknown, variables: unknown) => void;
        }) => ({
          mutate: (input: { featureId: string }) => {
            const next = mocks.toggleMutate(input);
            opts?.onSuccess?.(next, input);
          },
          isPending: false,
        }),
      },
    },
  },
}));

import { InterventionLikeButton } from "@/components/intervention/InterventionLikeButton";

const FEATURE_ID = "66666666-6666-4666-8666-666666666666";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.session = {
    data: { user: { id: "user-1" } },
    status: "authenticated",
  };
  mocks.likeStateQuery.mockReturnValue({
    data: { liked: false, count: 3 },
    isLoading: false,
    isError: false,
  });
  mocks.toggleMutate.mockReturnValue({ liked: true, count: 4 });
});

afterEach(cleanup);

describe("InterventionLikeButton", () => {
  it("renders the current count and the viewer's own like state", () => {
    render(<InterventionLikeButton featureId={FEATURE_ID} />);

    const button = screen.getByTestId("intervention-like-button");
    expect(button.getAttribute("data-liked")).toBe("false");
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByTestId("intervention-like-count").textContent).toBe("3");
    expect(mocks.likeStateQuery).toHaveBeenCalledWith(
      { featureId: FEATURE_ID },
      expect.anything()
    );
  });

  it("shows the viewer's existing like as pressed", () => {
    mocks.likeStateQuery.mockReturnValue({
      data: { liked: true, count: 9 },
      isLoading: false,
      isError: false,
    });
    render(<InterventionLikeButton featureId={FEATURE_ID} />);

    const button = screen.getByTestId("intervention-like-button");
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("intervention-like-count").textContent).toBe("9");
  });

  it("toggles and adopts the server's returned count without a remount", () => {
    render(<InterventionLikeButton featureId={FEATURE_ID} />);
    const button = screen.getByTestId("intervention-like-button");

    fireEvent.click(button);

    expect(mocks.toggleMutate).toHaveBeenCalledWith({ featureId: FEATURE_ID });
    // Same DOM node, new state: nothing re-mounted between the two assertions.
    expect(screen.getByTestId("intervention-like-button")).toBe(button);
    expect(button.getAttribute("data-liked")).toBe("true");
    expect(screen.getByTestId("intervention-like-count").textContent).toBe("4");
  });

  it("stays inert for a signed-out viewer and offers sign-in instead of erroring", () => {
    mocks.session = { data: null, status: "unauthenticated" };
    render(<InterventionLikeButton featureId={FEATURE_ID} />);

    const button = screen.getByTestId("intervention-like-button");
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(mocks.toggleMutate).not.toHaveBeenCalled();

    // `getLikeState` is a protectedProcedure, so the query must not even be
    // enabled for a signed-out viewer -- an UNAUTHORIZED here would surface as
    // a broken control rather than a sign-in prompt.
    expect(mocks.likeStateQuery).toHaveBeenCalledWith(
      { featureId: FEATURE_ID },
      expect.objectContaining({ enabled: false })
    );
    const link = screen.getByTestId("intervention-like-signin");
    expect(link.getAttribute("href")).toContain("/login");
  });
});
