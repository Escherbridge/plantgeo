/**
 * Why a toggle that no Parquet lane backs has nothing to draw. See
 * src/components/map/AGENTS.md "§non-lane-surfaces".
 */

import type { LayerToggleId } from "@/lib/map/layer-registry";

/** One surface's honest published state: why it is empty, and what would end that. */
export interface LayerPublicationStanding {
  /** Why this layer publishes nothing today. */
  reason: string;
  /** What has to happen upstream before it draws. */
  unblockedBy: string;
}

/**
 * Standings for the toggles the Parquet warehouse programme can never back.
 *
 * NOT `permanentlyUnavailableReason`: that field is a governance gate -- it disables the switch,
 * reads false in `useLayerVisibility` whatever `activeLayers` says, and drops the row out of
 * `DockSections`' group count -- and none of these three is withheld. Each is a live switch over
 * a real renderer that would paint the moment its upstream produced a row, so a standing states
 * the emptiness without asserting the layer is forbidden.
 *
 * `soil` is the fourth non-lane surface and is deliberately ABSENT: it is genuinely withheld and
 * already carries a `permanentlyUnavailableReason`, so a standing here would caption that row
 * twice. `layer-publication-standing.test.ts` holds both halves of that split.
 *
 * No entry may state a count or a date. A caption saying how many rows exist today is wrong the
 * next time a row lands, and a wrong caption is worse than the blank map it replaced -- the test
 * fails on any digit for exactly that reason.
 */
export const LAYER_PUBLICATION_STANDINGS: Partial<
  Record<LayerToggleId, LayerPublicationStanding>
> = {
  interventions: {
    reason:
      "Interventions are a community feature and stay in Postgres by design, so no warehouse " +
      "lane backs them.",
    unblockedBy:
      "An approved recommendation only reaches the map once a publish step runs, and nothing " +
      "invokes that step today, so the tile is empty even where recommendations exist.",
  },
  "strategy-recommendations": {
    reason:
      "Strategy recommendations come from a model that is not trained: its label plane holds " +
      "no labels a recommendation could be fit against.",
    unblockedBy:
      "Labelled outcomes have to land before any cell carries a recommendation, and no " +
      "warehouse lane will fill this surface in the meantime.",
  },
  "demand-heatmap": {
    reason:
      "The demand heatmap is derived from private strategy requests at the moment you ask for " +
      "it rather than stored per day, so it has no warehouse lane and no timeline.",
    unblockedBy:
      "A cell draws only once enough separate requests fall inside it to clear the anonymity " +
      "floor, so a sparse area is blank by design rather than by outage.",
  },
};

/** The caption a row states for a non-lane layer, or null when a warehouse lane backs it. */
export function layerPublicationStandingCaption(toggleId: LayerToggleId): string | null {
  const standing = LAYER_PUBLICATION_STANDINGS[toggleId];
  return standing === undefined ? null : `${standing.reason} ${standing.unblockedBy}`;
}
