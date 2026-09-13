"use client";

import { useMemo } from "react";
import { useLandContextStore } from "@/stores/land-context-store";
import { trpc } from "@/lib/trpc/client";
import { LandContextPanel } from "./LandContextPanel";
import { toLandContextPanelData } from "./adapter";
import type { LandContextResult } from "@/lib/server/services/land-context/types";

/**
 * Wires the persistent selection store + reference-plane reader into
 * `LandContextPanel`. This is the integration point between the map worker's
 * store/selection (`src/stores/land-context-store.ts`) and the panel
 * worker's presentational component + adapter (`./adapter.ts`).
 *
 * Re-resolves contacts for the currently browsed candidate
 * (`candidateIndex`), not the whole result set, so the panel's route
 * evidence stays pinned to one feature at a time per spec ("browse multiple
 * features without losing the selected project area").
 */
export function LandContextPanelHost() {
  const selection = useLandContextStore((state) => state.selection);
  const results = useLandContextStore((state) => state.results);
  const candidateIndex = useLandContextStore((state) => state.candidateIndex);
  const panelOpen = useLandContextStore((state) => state.panelOpen);
  const closePanel = useLandContextStore((state) => state.closePanel);

  const focused = candidateIndex !== null ? results[candidateIndex] : null;

  const contactsQuery = trpc.landContext.lookupContactsForSubject.useQuery(
    { subjectId: focused?.id ?? "", topic: null },
    { enabled: Boolean(focused) }
  );

  const panelData = useMemo(() => {
    if (!focused || !selection) return null;

    const contactResults: LandContextResult[] =
      contactsQuery.data && "status" in contactsQuery.data && contactsQuery.data.status === "ok"
        ? contactsQuery.data.data
        : [];

    const selectionLabel =
      selection.mode === "point" && selection.point
        ? `${selection.point[1].toFixed(5)}, ${selection.point[0].toFixed(5)}`
        : selection.mode === "parcel" && selection.parcelId
          ? `Parcel ${selection.parcelId}`
          : "Selected area";

    return toLandContextPanelData(contactResults, selectionLabel);
  }, [focused, selection, contactsQuery.data]);

  if (!panelOpen) return null;

  return <LandContextPanel data={panelData} onClose={closePanel} />;
}
