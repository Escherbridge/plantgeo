import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The map dock's Alerts section (2026-10-26 rejoin). The load-bearing properties: a signed-out
 * reader gets a sign-in prompt and issues no `protectedProcedure` query at all (same contract
 * `InterventionLikeButton.test.tsx` pins for the like control); a signed-in reader sees their
 * unread alerts and marking one read calls the mutation and invalidates the list + count queries
 * that fed the badge.
 */

const mocks = vi.hoisted(() => ({
  session: { status: "authenticated" as string },
  unreadCountQuery: vi.fn(),
  alertsQuery: vi.fn(),
  locationsQuery: vi.fn(),
  markReadMutate: vi.fn(),
  markAllReadMutate: vi.fn(),
  invalidate: {
    getAlerts: { invalidate: vi.fn() },
    getUnreadCount: { invalidate: vi.fn() },
    getWatchedLocations: { invalidate: vi.fn() },
    getSubscriptions: { invalidate: vi.fn() },
  },
}));

vi.mock("next-auth/react", () => ({
  useSession: () => mocks.session,
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    alerts: {
      getUnreadCount: { useQuery: mocks.unreadCountQuery },
      getAlerts: { useQuery: mocks.alertsQuery },
      getWatchedLocations: { useQuery: mocks.locationsQuery },
      markRead: {
        useMutation: (opts?: { onSuccess?: () => void }) => ({
          mutate: (input: { alertId: string }) => {
            mocks.markReadMutate(input);
            opts?.onSuccess?.();
          },
          isPending: false,
        }),
      },
      markAllRead: {
        useMutation: (opts?: { onSuccess?: () => void }) => ({
          mutate: () => {
            mocks.markAllReadMutate();
            opts?.onSuccess?.();
          },
          isPending: false,
        }),
      },
      addWatchedLocation: {
        useMutation: () => ({ mutate: vi.fn(), isPending: false }),
      },
    },
    useUtils: () => ({ alerts: mocks.invalidate }),
  },
}));

import { AlertsDetails } from "@/components/panels/AlertsDetails";

const MAP_CENTER = { lat: 43.6, lon: -116.2 };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.session = { status: "authenticated" };
  mocks.unreadCountQuery.mockReturnValue({ data: 1 });
  mocks.alertsQuery.mockReturnValue({
    data: [
      {
        id: "alert-1",
        alertType: "fire_proximity",
        severity: "critical",
        title: "Active fire detected 8 km away",
        body: "A NASA FIRMS fire detection was recorded near your watched location.",
        isRead: false,
      },
    ],
    isPending: false,
  });
  mocks.locationsQuery.mockReturnValue({ data: [], isPending: false });
});

afterEach(cleanup);

describe("AlertsDetails signed-out", () => {
  it("shows a sign-in prompt and issues no protected query", () => {
    mocks.session = { status: "unauthenticated" };
    render(<AlertsDetails mapCenter={MAP_CENTER} />);

    expect(screen.getByTestId("alerts-signin-prompt")).toBeTruthy();
    expect(mocks.unreadCountQuery).toHaveBeenCalledWith(undefined, expect.objectContaining({ enabled: false }));
    expect(mocks.alertsQuery).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ enabled: false })
    );
  });
});

describe("AlertsDetails signed-in", () => {
  it("lists the caller's own alerts with the unread count in the heading", () => {
    render(<AlertsDetails mapCenter={MAP_CENTER} />);

    expect(screen.getByText(/Alerts \(1 unread\)/)).toBeTruthy();
    expect(screen.getByText("Active fire detected 8 km away")).toBeTruthy();
  });

  it("marking an alert read calls the mutation and refreshes the list and the count", () => {
    render(<AlertsDetails mapCenter={MAP_CENTER} />);

    fireEvent.click(screen.getByLabelText("Mark as read"));

    expect(mocks.markReadMutate).toHaveBeenCalledWith({ alertId: "alert-1" });
    expect(mocks.invalidate.getAlerts.invalidate).toHaveBeenCalledTimes(1);
    expect(mocks.invalidate.getUnreadCount.invalidate).toHaveBeenCalledTimes(1);
  });

  it("shows an empty state instead of a false zero when there are no alerts yet", () => {
    mocks.alertsQuery.mockReturnValue({ data: [], isPending: false });
    render(<AlertsDetails mapCenter={MAP_CENTER} />);

    expect(
      screen.getByText("No alerts yet. Add a watched location below to start monitoring it.")
    ).toBeTruthy();
  });
});
