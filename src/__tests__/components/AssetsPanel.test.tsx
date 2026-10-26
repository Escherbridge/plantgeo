import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

/*
 * Stubs the dynamically-imported route map the same way InterventionSubmitModal.test.tsx stubs
 * InterventionDrawControl: this file exercises the panel's own wiring (the create form's
 * payload, the disabled-until-named guard), not a real MapLibre instance in jsdom.
 */
vi.mock("next/dynamic", () => ({
  default: () =>
    function AssetRouteMapStub() {
      return <div data-testid="route-map-stub" />;
    },
}));

const mocks = vi.hoisted(() => ({
  createAssetMutate: vi.fn(),
  invalidateListAssets: vi.fn(),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    useUtils: () => ({
      tracking: {
        listAssets: { invalidate: mocks.invalidateListAssets },
        getRouteHistory: { invalidate: vi.fn() },
      },
    }),
    tracking: {
      listAssets: {
        useQuery: () => ({
          data: [
            {
              id: "11111111-1111-4111-8111-111111111111",
              name: "Crew truck 1",
              type: "vehicle",
              status: "offline",
            },
          ],
          isLoading: false,
          isError: false,
        }),
      },
      getLastPositions: {
        useQuery: () => ({ data: [], isLoading: false, isError: false }),
      },
      getRouteHistory: {
        useQuery: () => ({ data: undefined, isLoading: false, isError: false }),
      },
      createAsset: {
        useMutation: (options: { onSuccess?: () => void }) => ({
          mutate: (input: unknown) => {
            mocks.createAssetMutate(input);
            options.onSuccess?.();
          },
          isPending: false,
        }),
      },
    },
  },
}));

import { AssetsPanel } from "@/components/admin/tracking/AssetsPanel";

describe("AssetsPanel", () => {
  beforeEach(() => {
    mocks.createAssetMutate.mockClear();
    mocks.invalidateListAssets.mockClear();
  });

  it("renders the already-registered asset from listAssets", () => {
    render(<AssetsPanel />);
    expect(screen.getByText("Crew truck 1")).toBeInTheDocument();
  });

  it("disables the add-asset submit button until a name is entered", () => {
    render(<AssetsPanel />);
    const submitButton = screen.getByRole("button", { name: /Add asset/ });
    expect(submitButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("New asset name"), {
      target: { value: "Crew truck 2" },
    });
    expect(submitButton).not.toBeDisabled();
  });

  it("submits the typed name, type and status to createAsset and invalidates the list", () => {
    render(<AssetsPanel />);

    fireEvent.change(screen.getByLabelText("New asset name"), {
      target: { value: "Crew truck 2" },
    });
    fireEvent.change(screen.getByLabelText("Asset type"), {
      target: { value: "crew" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add asset/ }));

    expect(mocks.createAssetMutate).toHaveBeenCalledWith({
      name: "Crew truck 2",
      type: "crew",
      status: "offline",
    });
    expect(mocks.invalidateListAssets).toHaveBeenCalledTimes(1);
  });
});
