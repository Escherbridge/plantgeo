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
      "Shows community intervention recommendations that have been reviewed and published.",
    unblockedBy:
      "New recommendations stay in the review queue until an expert approves them for the map.",
  },
  "intervention-drafts": {
    reason:
      "Shows your own draft recommendations and the wider review queue to signed-in readers, " +
      "read from a client-side query rather than a warehouse lane.",
    unblockedBy:
      "Sign in to see it; nothing here waits on a lane, since it is never fed by one.",
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
      "Shows where people have requested help while protecting individual request locations.",
    unblockedBy:
      "Areas with too few separate requests remain blank for privacy.",
  },
};

/** The caption a row states for a non-lane layer, or null when a warehouse lane backs it. */
export function layerPublicationStandingCaption(toggleId: LayerToggleId): string | null {
  const standing = LAYER_PUBLICATION_STANDINGS[toggleId];
  return standing === undefined ? null : `${standing.reason} ${standing.unblockedBy}`;
}
