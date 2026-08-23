/**
 * The uniform zoom ladder every layer's Parquet partitions are written under, and which tier
 * serves a given map zoom.
 *
 * Mirrors `services/agri-data-service/src/agri_data_service/foundation/parquet/zoom.py`
 * exactly: same four breakpoints, same "serve from the tier at or below the request" rule, and
 * the same two-digit zero-padded path segment (`zoom=09`, not `zoom=9` -- `zoom.py`'s
 * `ZOOM_SEGMENT_DIGITS` and its `paths.py` regex both require exactly two digits). The two
 * implementations must never diverge: a client that resolves a different tier than the writer
 * published requests a partition path that was never written and silently reads nothing where
 * data exists.
 *
 * ONE ladder for every layer, deliberately -- see `zoom.py`'s header for the per-layer-ladder
 * cost this replaces (watersheds at 4/6/8/10/12, strategy recommendations at 6/11, neither
 * nameable without knowing the layer's private table). Tiers are named by their minimum zoom,
 * never an adjective like "coarse" or "regional", so a tier's name stays true even if the
 * ladder itself ever grows another rung.
 *
 * This module owns ONLY the ladder and its resolution. The two frozen per-layer ladders it is
 * meant to eventually replace -- `SOIL_SURVEY_TIERS` (usda-soil.ts) and `SOIL_FIELD_TIERS`
 * (environmental-read-model.ts) -- are untouched here; repointing their consumers is a later
 * slice against a contract this module does not yet declare frozen.
 */

/** The four tiers every layer publishes at, ordered low to high. */
export const ZOOM_TIERS = [0, 5, 9, 13] as const;

/** One rung of the ladder above -- the literal union `ZOOM_TIERS` enumerates. */
export type ZoomTier = (typeof ZOOM_TIERS)[number];

/** Digits a rendered `zoom=` path segment is zero-padded to; must match `ZOOM_SEGMENT_DIGITS` in zoom.py. */
const ZOOM_SEGMENT_DIGITS = 2;

/**
 * Raised when a map zoom cannot be resolved to a published tier at all. Never returned as a
 * guessed tier: a caller that produced an impossible zoom (negative, non-finite) has a bug
 * upstream, and answering it with `z0` would hide that bug behind a path that happens to exist.
 */
export class ZoomTierResolutionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ZoomTierResolutionError";
  }
}

/**
 * Resolve a MapLibre map zoom DOWN to the tier that serves it: the highest published rung at or
 * below the zoom (z11 resolves to z9, z13 and above resolves to z13, z3 resolves to z0).
 *
 * Fractional zooms (MapLibre reports e.g. 11.4) are compared directly against the integer
 * breakpoints with no rounding step -- flooring first would change nothing given integer
 * breakpoints, and would only invite a rounding-direction bug the Python side has no equivalent
 * of. There is no ceiling above the top tier: any zoom at or beyond it resolves to the top tier,
 * because that tier's partitions already answer every zoom up to the web-map maximum. A zoom
 * below the floor tier, or a non-finite value (`NaN`, `Infinity`, `-Infinity`), throws rather
 * than resolving to a guessed tier -- see `ZoomTierResolutionError`.
 */
export function resolveZoomTier(mapZoom: number): ZoomTier {
  if (!Number.isFinite(mapZoom)) {
    throw new ZoomTierResolutionError(
      `map zoom ${mapZoom} is not a finite number; no published tier can serve it`
    );
  }
  const floorTier = ZOOM_TIERS[0];
  if (mapZoom < floorTier) {
    throw new ZoomTierResolutionError(
      `map zoom ${mapZoom} is below the ladder's floor tier (z${floorTier}); no published partition serves it`
    );
  }
  let servingTier: ZoomTier = floorTier;
  for (const tier of ZOOM_TIERS) {
    if (tier <= mapZoom) servingTier = tier;
  }
  return servingTier;
}

/**
 * Render a tier into its `zoom=` partition path segment -- the one place the path string is
 * built, so no call site hand-interpolates a differently-padded literal. Zero-padded to
 * `ZOOM_SEGMENT_DIGITS` digits (`zoom=09`, not `zoom=9`) to match `zoom_prefix` in zoom.py.
 */
export function zoomTierPathSegment(tier: ZoomTier): string {
  return `zoom=${tier.toString().padStart(ZOOM_SEGMENT_DIGITS, "0")}`;
}
