/**
 * Choosing WHICH published rung answers a viewport, from zoom and bbox size together.
 *
 * Rationale, the two call sites and the owner decision this encodes: see `src/lib/map/AGENTS.md`
 * section "Rung selection".
 */

/** One rung ladder plus the viewport being placed on it. Rungs are named by the caller's vocabulary. */
export interface RungSelectionRequest<TRung extends string | number> {
  /** Every rung of the ladder, coarsest first. Index order IS the ladder; nothing else defines it. */
  coarsestFirst: readonly TRung[];
  /** The widest bbox, in square degrees, each rung will answer. Must grow coarser-ward. */
  maxBboxSquareDegrees: Readonly<Record<TRung, number>>;
  /** The requested viewport's area in square degrees. */
  areaSquareDegrees: number;
  /**
   * The finest rung this request may be served from -- normally the one the map zoom selects.
   * A rung finer than this is never chosen, because serving z5 from a detail rung would answer a
   * continental question with point evidence. Omitted means the ladder's finest rung.
   */
  finestAllowed?: TRung;
  /** Optional publication gate: a rung the warehouse has not published is not selectable. */
  isPublished?: (rung: TRung) => boolean;
}

/**
 * The FINEST published rung, no finer than `finestAllowed`, whose ceiling admits this bbox area --
 * or null when no rung on the ladder does.
 *
 * Returning null rather than the coarsest rung is the point: a viewport no rung admits must be
 * REFUSED, never silently answered from a rung that was not asked for. Coarsening without saying so
 * answers a question about one area with evidence about another.
 */
export function selectFinestAdmittingRung<TRung extends string | number>(
  request: RungSelectionRequest<TRung>
): TRung | null {
  const { coarsestFirst, maxBboxSquareDegrees, areaSquareDegrees, finestAllowed, isPublished } =
    request;
  const finestIndex =
    finestAllowed === undefined ? coarsestFirst.length - 1 : coarsestFirst.indexOf(finestAllowed);
  if (finestIndex < 0) return null;
  // Walk finest-to-coarsest from the allowed ceiling: the first rung that admits the area is the
  // finest one that does, because a ladder's ceilings only grow coarser-ward.
  for (let index = finestIndex; index >= 0; index--) {
    const rung = coarsestFirst[index];
    if (isPublished !== undefined && !isPublished(rung)) continue;
    if (areaSquareDegrees <= maxBboxSquareDegrees[rung]) return rung;
  }
  return null;
}
