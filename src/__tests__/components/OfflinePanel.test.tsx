import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, fireEvent, act } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { OfflinePanel } from "@/components/panels/OfflinePanel";
import { useOfflineSync } from "@/hooks/useOfflineSync";
import type { ConflictedOperation } from "@/lib/offline/sync-queue";

/**
 * `OfflinePanel` is the dock's Offline & Sync section body now (see
 * `src/components/map/AGENTS.md` "One manager, no floating surfaces") -- no close prop, no
 * Escape handling, no positioning of its own. Collapsing the section is what closes it.
 *
 * `useOfflineSync` is mocked throughout so the conflict-resolution tests can assert against a
 * controlled `conflicts` array; the storage/download tests set it to the same empty defaults
 * the real hook falls back to when jsdom's missing `indexedDB` makes its queue/conflict reads
 * reject (see `useOfflineSync.test.ts` for that real-hook coverage).
 */
vi.mock("@/hooks/useOfflineSync", () => ({
  useOfflineSync: vi.fn(),
}));

const mockUseOfflineSync = vi.mocked(useOfflineSync);

const resolveConflict = vi.fn().mockResolvedValue(undefined);

function makeConflict(overrides: Partial<ConflictedOperation>): ConflictedOperation {
  return {
    id: "op-1",
    type: "update",
    resource: "interventions",
    data: {},
    timestamp: 1,
    updatedAt: 1,
    conflictReason: "server-409",
    detectedAt: 2,
    ...overrides,
  };
}

async function flushPendingReads() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("OfflinePanel", () => {
  beforeEach(() => {
    resolveConflict.mockClear();
    mockUseOfflineSync.mockReturnValue({
      isOnline: true,
      pendingCount: 0,
      isSyncing: false,
      conflicts: [],
      resolveConflict,
    });
  });

  it("renders storage stats and the download controls", async () => {
    renderWithProviders(<OfflinePanel />);
    await flushPendingReads();

    expect(screen.getByText("Cache Storage Used")).toBeTruthy();
    expect(screen.getByRole("button", { name: /clear cache/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /download selected region/i })).toBeTruthy();
    expect(screen.getByText(/min zoom level/i)).toBeTruthy();
    expect(screen.getByText(/max zoom level/i)).toBeTruthy();
  });

  it("renders no Sync Conflicts section when there are no conflicts", async () => {
    renderWithProviders(<OfflinePanel />);
    await flushPendingReads();

    expect(screen.queryByText(/sync conflicts/i)).toBeNull();
  });

  describe("with sync conflicts", () => {
    beforeEach(() => {
      mockUseOfflineSync.mockReturnValue({
        isOnline: true,
        pendingCount: 0,
        isSyncing: false,
        conflicts: [
          makeConflict({ id: "op-1", conflictReason: "server-409", resource: "interventions" }),
          makeConflict({
            id: "op-2",
            type: "create",
            conflictReason: "stale-snapshot",
            resource: "sensors",
          }),
        ],
        resolveConflict,
      });
    });

    it("renders the Sync Conflicts section with both reason labels", async () => {
      renderWithProviders(<OfflinePanel />);
      await flushPendingReads();

      expect(screen.getByText("Sync Conflicts (2)")).toBeTruthy();
      expect(screen.getByText("Server rejected")).toBeTruthy();
      expect(screen.getByText("Newer on server")).toBeTruthy();
    });

    it("resolves the right conflict with keep-local", async () => {
      renderWithProviders(<OfflinePanel />);
      await flushPendingReads();

      const keepLocalButtons = screen.getAllByRole("button", { name: /keep local/i });
      expect(keepLocalButtons).toHaveLength(2);

      fireEvent.click(keepLocalButtons[0]);

      expect(resolveConflict).toHaveBeenCalledWith("op-1", "keep-local");
    });

    it("resolves the right conflict with discard-local", async () => {
      renderWithProviders(<OfflinePanel />);
      await flushPendingReads();

      const discardLocalButtons = screen.getAllByRole("button", { name: /discard local/i });
      expect(discardLocalButtons).toHaveLength(2);

      fireEvent.click(discardLocalButtons[1]);

      expect(resolveConflict).toHaveBeenCalledWith("op-2", "discard-local");
    });
  });
});
