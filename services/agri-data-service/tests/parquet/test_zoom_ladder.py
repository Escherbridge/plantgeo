"""The uniform zoom ladder: which tiers exist, and which one answers an arbitrary requested zoom."""

from __future__ import annotations

import pytest

from agri_data_service.foundation.parquet.zoom import (
    MAX_REQUEST_ZOOM,
    MIN_REQUEST_ZOOM,
    ZOOM_TIERS,
    ZoomTier,
    ZoomTierError,
    serving_zoom_tier,
    validate_zoom_tier,
    zoom_tier_span,
)


def test_the_ladder_is_pinned() -> None:
    """Every layer publishes these four tiers and no others; a lane inventing a fifth is a defect."""
    assert ZOOM_TIERS == (0, 5, 9, 13)


def test_the_ladder_is_ascending_and_starts_at_the_whole_world() -> None:
    """Resolution walks down from any request to a rung below it, so the floor rung must be z0."""
    assert list(ZOOM_TIERS) == sorted(ZOOM_TIERS)
    assert ZOOM_TIERS[0] == MIN_REQUEST_ZOOM


@pytest.mark.parametrize("tier", ZOOM_TIERS)
def test_validation_accepts_every_published_tier(tier: ZoomTier) -> None:
    assert validate_zoom_tier(tier) == tier


@pytest.mark.parametrize("zoom", [-1, 1, 4, 6, 8, 10, 12, 14, 22, 100])
def test_validation_rejects_a_zoom_that_is_not_a_tier(zoom: int) -> None:
    """z11 is a legitimate request and an illegitimate tier; only the ladder may name a directory."""
    with pytest.raises(ZoomTierError, match="not one of the published tiers"):
        validate_zoom_tier(zoom)  # type: ignore[arg-type]


def test_validation_states_the_consequence_not_only_the_condition() -> None:
    with pytest.raises(ZoomTierError, match="no reader resolves"):
        validate_zoom_tier(11)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (0, 0),
        (1, 0),
        (4, 0),
        (5, 5),
        (6, 5),
        (8, 5),
        (9, 9),
        (11, 9),
        (12, 9),
        (13, 13),
        (14, 13),
        (MAX_REQUEST_ZOOM, 13),
    ],
)
def test_a_request_is_served_by_the_rung_at_or_below_it(requested: int, expected: ZoomTier) -> None:
    """z11 is served by the z9 tier: the most detailed geometry actually published at or under it."""
    assert serving_zoom_tier(requested) == expected


@pytest.mark.parametrize("requested", [-1, MAX_REQUEST_ZOOM + 1, 99])
def test_a_request_off_the_web_map_scale_is_refused(requested: int) -> None:
    """Resolving it would hand a tier to a viewport that cannot exist, silently."""
    with pytest.raises(ZoomTierError, match="outside the web-map scale"):
        serving_zoom_tier(requested)


def test_every_zoom_on_the_scale_resolves_to_exactly_one_tier() -> None:
    """A request the ladder cannot answer would fall through to no tier at all."""
    resolved = [serving_zoom_tier(zoom) for zoom in range(MIN_REQUEST_ZOOM, MAX_REQUEST_ZOOM + 1)]

    assert set(resolved) == set(ZOOM_TIERS)
    assert resolved == sorted(resolved)


def test_tier_spans_tile_the_whole_scale_without_overlap() -> None:
    """A gap between two rungs is a request nobody serves; an overlap is two tiers claiming one zoom."""
    spans = [zoom_tier_span(tier) for tier in ZOOM_TIERS]

    assert spans == [(0, 4), (5, 8), (9, 12), (13, MAX_REQUEST_ZOOM)]
    assert [zoom for first, last in spans for zoom in range(first, last + 1)] == list(
        range(MIN_REQUEST_ZOOM, MAX_REQUEST_ZOOM + 1)
    )


@pytest.mark.parametrize("tier", ZOOM_TIERS)
def test_a_tier_serves_every_zoom_in_its_own_span(tier: ZoomTier) -> None:
    first, last = zoom_tier_span(tier)

    assert all(serving_zoom_tier(zoom) == tier for zoom in range(first, last + 1))


def test_tier_span_refuses_a_zoom_off_the_ladder() -> None:
    with pytest.raises(ZoomTierError):
        zoom_tier_span(11)  # type: ignore[arg-type]
