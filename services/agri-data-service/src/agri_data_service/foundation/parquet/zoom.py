"""The uniform zoom ladder: the four tiers every layer publishes, and which one serves a request.

Layer L0: stdlib only. May NOT import any first-party module outside `foundation`, nor SQLAlchemy,
httpx, asyncpg, or click. See `AGENTS.md` in this directory for where `zoom=` sits in the object key
and why it sits there.

ONE ladder serves every layer, deliberately. The PostGIS era cut per-layer breakpoints -- watersheds
at 4/6/8/10/12 (`drizzle/0023_watershed_zoom_generalization.sql:149-155`), strategy recommendations
at 6/11 (`drizzle/0028_strategy_recommendations_real_geometry.sql:376,393`) -- and the cost was that no reader
could name a path until it knew its layer's private table. A uniform ladder makes serving a path
template; a per-layer ladder makes it a lookup that every new layer must remember to populate.

Tiers are named by their MINIMUM zoom rather than by an adjective, because an adjective stops being
true the moment the ladder changes: `coarse` means one thing in a three-rung ladder and another in a
four-rung one, while the tier that begins at z9 begins at z9 forever.

A request landing between two rungs is served by the rung BELOW it -- z11 reads the z9 tier. That
tier is the most detailed geometry actually published at or under the requested zoom. Rounding up
would answer a z11 viewport with z13 bytes nobody budgeted for, and would silently claim a
resolution the writer never generalised to.
"""

from __future__ import annotations

from typing import Final, Literal

ZoomTier = Literal[0, 5, 9, 13]

ZOOM_TIERS: Final[tuple[ZoomTier, ...]] = (0, 5, 9, 13)

# The web-map scale a request may name; Martin's composite advertises maxzoom 22 and nothing above
# it is a viewport a client can hold.
MIN_REQUEST_ZOOM: Final = 0
MAX_REQUEST_ZOOM: Final = 22


class ZoomTierError(ValueError):
    """Raised when a zoom is not a published tier, or a requested zoom is off the web-map scale."""


def validate_zoom_tier(zoom: int) -> ZoomTier:
    """Return `zoom` narrowed to a tier every lane publishes, else raise."""
    for tier in ZOOM_TIERS:
        if zoom == tier:
            return tier
    raise ZoomTierError(
        f"zoom {zoom!r} is not one of the published tiers {ZOOM_TIERS}; writing it would strand the "
        f"rows under a prefix no reader resolves, and serving would answer from a tier that was never written"
    )


def serving_zoom_tier(requested_zoom: int) -> ZoomTier:
    """Return the published tier answering `requested_zoom`: the highest rung at or below it."""
    if requested_zoom < MIN_REQUEST_ZOOM or requested_zoom > MAX_REQUEST_ZOOM:
        raise ZoomTierError(
            f"requested zoom {requested_zoom!r} is outside the web-map scale "
            f"{MIN_REQUEST_ZOOM}..{MAX_REQUEST_ZOOM}; resolving it would hand a tier to a viewport that "
            f"cannot exist and answer a nonsense request as though it were routine"
        )
    for tier in reversed(ZOOM_TIERS):
        if tier <= requested_zoom:
            return tier
    raise ZoomTierError(
        f"no published tier sits at or below zoom {requested_zoom}: the ladder {ZOOM_TIERS} has no floor rung, "
        f"so every whole-world request would resolve to a tier nobody wrote"
    )


def zoom_tier_span(tier: ZoomTier) -> tuple[int, int]:
    """Return the inclusive range of requested zooms `tier` answers, up to the top of the web-map scale."""
    validated = validate_zoom_tier(tier)
    rung = ZOOM_TIERS.index(validated)
    if rung + 1 == len(ZOOM_TIERS):
        return (validated, MAX_REQUEST_ZOOM)
    return (validated, ZOOM_TIERS[rung + 1] - 1)
