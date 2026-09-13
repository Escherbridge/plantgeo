/**
 * The one record shape the intervention detail modal renders, whichever of the
 * two origins resolved it.
 *
 * A drafts-overlay click fills this from memory (`useInterventionDraftsOverlay`
 * already holds every field for the caller's own rows); a published Martin-tile
 * click fills it from `interventions.getInterventionDetail`, because a vector
 * tile carries a simplified geometry and only the columns the tile function
 * projects. Both paths agree on this type so the modal never branches on origin.
 *
 * Every field but `id`, `status` and `geometry` is nullable: a row proposed by
 * another contributor reaches the overlay through `listProposed`, which projects
 * a centroid and no authorship or timestamps. The modal renders an absent field
 * as unknown rather than inventing one.
 */
export interface InterventionDetailRecord {
  id: string;
  name: string | null;
  type: string | null;
  category: string | null;
  /**
   * Read exactly as `contributions.publishContribution` / `rejectContribution`
   * write it (`published` / `rejected` / `pending_review`). The parallel
   * `castModerationVote` vocabulary (`approved`/`active`/`monitored`) is not
   * honoured by `geo.intervention_tiles` and is never presented as live state.
   */
  status: string;
  /**
   * Which of the two things sharing the `interventions` layer this row is
   * (`properties.kind`, track `public_strategy_requests_20260913`): a public
   * strategy request -- somebody asking for an intervention here -- or a drawn
   * recommendation.
   *
   * Optional, and absent means `"intervention"`: every row written before
   * 2026-09-13 predates the discriminator and is deliberately not backfilled, so
   * readers default rather than fail. The drafts overlay leaves it unset for the
   * same reason -- a draft is never a request, since a request publishes
   * immediately and so never reaches the draft path.
   */
  kind?: "intervention" | "request" | null;
  description: string | null;
  /** The real drawn geometry, not a centroid -- null only when the row has none. */
  geometry: GeoJSON.Geometry | null;
  submittedByUserId: string | null;
  submittedByTeamId: string | null;
  createdAt: Date | string | null;
  updatedAt: Date | string | null;
  /** Only ever meaningful when `status === "rejected"`; null otherwise. */
  reviewNote: string | null;
  /**
   * True when the geometry above is the full drawn shape. False when it is a
   * centroid stand-in (`listProposed`'s projection for another contributor's
   * consenting draft), so the modal can say so instead of drawing an outline
   * that was never submitted.
   */
  hasFullGeometry: boolean;
}
