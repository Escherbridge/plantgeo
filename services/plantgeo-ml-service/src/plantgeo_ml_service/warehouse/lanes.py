"""What each lane's partition day MEANS: its publication lag, history floor, cadence and forecaster.

Layer L2. A COPY of the contract half of agri-data-service's `pipeline/parquet/lane_registry.py`
(the adapters and watermark resolvers do not cross), held honest by `tests/test_lanes_parity.py`.
Why a feature never reads past `as_of - publication_lag_days` lives in `AGENTS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

from plantgeo_ml_service.foundation.parquet_paths import validate_layer_slug

LaneNature = Literal["daily_series", "release_series", "static_lookup"]


class LaneContractError(ValueError):
    """Raised when a lane contract cannot hold, or a lane this service does not know is asked for."""


@dataclass(frozen=True, slots=True)
class LaneContract:
    """One stream's clock: when its day settles, how far back it goes, and how often it publishes."""

    slug: str
    history_floor: date
    publication_lag_days: int
    nature: LaneNature
    cadence_days: int = 1
    #: The `method/monte_carlo` module stem that forecasts this lane, or None for `horizon: none`.
    forecast_module: str | None = None

    def __post_init__(self) -> None:
        validate_layer_slug(self.slug)
        if self.publication_lag_days < 0:
            raise LaneContractError(f"lane {self.slug!r} declares a negative publication lag")
        if self.cadence_days < 1:
            raise LaneContractError(f"lane {self.slug!r} declares a cadence of under one day")

    def settled_through(self, as_of: date) -> date:
        """Return the newest day this lane's producer could have published by `as_of`.

        The whole leakage guard in one line: a feature built for `as_of` may read this day and no
        later one, whatever the bucket happens to hold. A partition newer than this exists only
        because some other process ran ahead of the lane's own clock.
        """
        return as_of - timedelta(days=self.publication_lag_days)


#: Measured 2026-09-19 against `agri_data_service.pipeline.parquet.lane_registry.LANE_REGISTRY`.
#: Only the six streams this service consumes are copied; the sibling registers thirty-two.
LANE_CONTRACTS: Final[dict[str, LaneContract]] = {
    contract.slug: contract
    for contract in (
        LaneContract(
            slug="signal",
            history_floor=date(2022, 4, 30),
            publication_lag_days=9,
            nature="daily_series",
            forecast_module="signal",
        ),
        LaneContract(
            slug="fire-detections",
            history_floor=date(2000, 11, 1),
            publication_lag_days=2,
            nature="daily_series",
            forecast_module="fire_detections",
        ),
        LaneContract(
            slug="vegetation",
            history_floor=date(2022, 8, 5),
            publication_lag_days=7,
            nature="daily_series",
            # The one forecaster whose module stem is NOT its slug; it predates `layer-lanes.md`.
            forecast_module="vegetation_ndvi_forecast",
        ),
        LaneContract(
            slug="drought",
            history_floor=date(2022, 8, 9),
            publication_lag_days=4,
            nature="release_series",
            cadence_days=7,
        ),
        LaneContract(
            slug="burn-severity",
            history_floor=date(2020, 11, 24),
            publication_lag_days=7,
            nature="release_series",
        ),
        LaneContract(
            slug="weather-observations",
            history_floor=date(2026, 8, 1),
            publication_lag_days=2,
            nature="daily_series",
        ),
    )
}

#: The flat projection the feature builders read, so a leakage guard never re-derives a lag.
PUBLICATION_LAG_DAYS: Final[dict[str, int]] = {
    slug: contract.publication_lag_days for slug, contract in LANE_CONTRACTS.items()
}


def lane_contract(slug: str) -> LaneContract:
    """Return one lane's contract, or refuse by naming the lanes this service knows."""
    contract = LANE_CONTRACTS.get(slug)
    if contract is None:
        raise LaneContractError(
            f"lane {slug!r} has no contract in this service; it knows {tuple(sorted(LANE_CONTRACTS))}"
        )
    return contract


def settled_through(slug: str, as_of: date) -> date:
    """Return the newest day of `slug` a feature issued on `as_of` may read."""
    return lane_contract(slug).settled_through(as_of)
