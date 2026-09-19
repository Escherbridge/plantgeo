"""`forecast-summary`: one lane, one point, one valid day, on whichever path that lane publishes on.

Layer L4. Two paths, because two lanes mean two different things by "tomorrow": rationale, and the
`layer-lanes.md` section 2 carve-out that creates the second one, live in `AGENTS.md` in this
directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from plantgeo_ml_service.foundation.parquet_paths import BASE_PARTITION_ZOOM
from plantgeo_ml_service.planes import refusals
from plantgeo_ml_service.planes.availability_reads import read_lane_availability
from plantgeo_ml_service.planes.partition_reads import (
    CELL_POSITION_COLUMNS,
    MAX_POINT_ROWS,
    POINT_POSITION_COLUMNS,
    CellPosition,
    PositionColumns,
    day_part_keys,
    nearest_cell_position,
    newest_valid_day,
    rows_at_cell_position,
)
from plantgeo_ml_service.planes.wire import (
    ARTIFACT_ABSENT_NO_ARTIFACT,
    ARTIFACT_ABSENT_NOT_MODEL_BACKED,
    ARTIFACT_ABSENT_PROVENANCE_CONFLICTED,
    SERVING_PATH_FORECAST_KIND,
    SERVING_PATH_RELEASE_SERIES,
    ClaimProvenance,
    ServingPath,
    render_day,
    render_row,
)
from plantgeo_ml_service.warehouse.lanes import forecast_root_kind, serving_path
from plantgeo_ml_service.warehouse.streams import (
    POINT_QUANTILE,
    StreamSchemaError,
    registered_stream_names,
    stream_schema,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession
    from plantgeo_ml_service.pipeline.object_store import ReadOnlyObjectStore
    from plantgeo_ml_service.planes.availability_reads import LaneAvailability

#: The column a release-series issue file carries its future in.
VALID_TIME_COLUMN: Final = "valid_time"

#: The column a model-backed forecast row names its artifact in. Only `fire-risk` carries it.
ARTIFACT_COLUMN: Final = "model_artifact_sha256"

#: `quantile` on a deterministic lane is the POINT label, not a number. Stated here because this
#: reader renders whatever the column holds and must not assume a float
#: (`layer-lanes.md` section 3, amended 2026-09-19).
ACCEPTED_DETERMINISTIC_QUANTILE: Final = POINT_QUANTILE

#: The sentinel a fire-risk run with no artifact records instead of a digest.
NO_ARTIFACT_SENTINEL: Final = "no-artifact"


@dataclass(frozen=True, slots=True)
class ForecastSummary:
    """One lane's answer for one point and one valid day, with the path the web must read it on."""

    layer: str
    serving_path: ServingPath
    position: CellPosition
    valid_day: date
    rows: tuple[Mapping[str, object], ...]
    truncated: bool
    availability: LaneAvailability
    #: MEASURED on both paths: forecast lanes from their generation, release lanes from the newest
    #: `valid_time` in the issue file the generation points at.
    published_horizon_days: int
    claim: ClaimProvenance

    def to_wire(self) -> dict[str, object]:
        """Render the summary payload; the claim block is added by the route."""
        return {
            "layer": self.layer,
            "serving_path": self.serving_path,
            "cell_longitude": self.position.longitude,
            "cell_latitude": self.position.latitude,
            "valid_day": render_day(self.valid_day),
            "issue_day": render_day(self.availability.issue_day),
            "source_ceiling": render_day(self.availability.source_ceiling),
            "published_horizon_days": self.published_horizon_days,
            "generation_key": self.availability.generation_key,
            "rows": [render_row(row) for row in self.rows],
            "truncated": self.truncated,
            "availability": self.availability.to_wire(),
        }


def serving_path_for(layer: str) -> ServingPath:
    """Return which path one lane publishes its future on, refusing a lane this service cannot name."""
    if serving_path(layer) == SERVING_PATH_RELEASE_SERIES:
        return SERVING_PATH_RELEASE_SERIES
    try:
        stream_schema(layer, forecast_root_kind(layer))
    except StreamSchemaError as error:
        if layer in registered_stream_names():
            raise refusals.lane_not_forecast(layer=layer, detail=str(error)) from error
        raise refusals.lane_unknown(layer=layer, known=registered_stream_names()) from error
    return SERVING_PATH_FORECAST_KIND


def read_forecast_summary(  # noqa: PLR0913 - one keyword per bound the read is narrowed by
    store: ReadOnlyObjectStore,
    session: DuckDbSession,
    *,
    layer: str,
    longitude: float,
    latitude: float,
    day: date,
) -> ForecastSummary:
    """Return one lane's rows for one point and one valid day, or refuse with a typed reason.

    The PATH is decided first, and the availability root follows from it: a release-series lane is
    judged against its `kind=observed` pointer, because its `kind=forecast` root is reserved and
    never written. Looking the pointer up first is how a healthy lane answered
    `availability_unpublished` on production `96831d8b`.
    """
    path = serving_path_for(layer)
    if path == SERVING_PATH_RELEASE_SERIES:
        return _read_release_series(store, session, layer=layer, longitude=longitude, latitude=latitude, day=day)
    return _read_forecast_kind(store, session, layer=layer, longitude=longitude, latitude=latitude, day=day)


def _read_forecast_kind(  # noqa: PLR0913 - one keyword per bound the read is narrowed by
    store: ReadOnlyObjectStore,
    session: DuckDbSession,
    *,
    layer: str,
    longitude: float,
    latitude: float,
    day: date,
) -> ForecastSummary:
    """Answer from `layer=<slug>/kind=forecast`, whose partition day IS the valid day."""
    kind = forecast_root_kind(layer)
    availability = read_lane_availability(store, layer=layer, kind=kind, generation_days="forecast_days")
    if not availability.covers(day):
        raise refusals.availability_day_not_covered(
            layer=layer,
            kind=kind,
            day=day.isoformat(),
            detail=f"{render_day(availability.earliest_terminal_day)}..{render_day(availability.latest_terminal_day)}",
        )
    keys = day_part_keys(store, layer=layer, kind=kind, zoom=BASE_PARTITION_ZOOM, day=day)
    position = _nearest(session, keys, layer=layer, day=day, longitude=longitude, latitude=latitude)
    answer = rows_at_cell_position(session, keys, position=position, row_limit=MAX_POINT_ROWS)
    if not answer.rows:
        raise refusals.cell_series_absent(
            layer=layer, cell_id=f"{position.longitude},{position.latitude}", origin=day.isoformat()
        )
    return ForecastSummary(
        layer=layer,
        serving_path=SERVING_PATH_FORECAST_KIND,
        position=position,
        valid_day=day,
        rows=answer.rows,
        truncated=answer.truncated,
        availability=availability,
        # Measured by the generation; `forecast_days` always fills it.
        published_horizon_days=availability.published_horizon_days or 0,
        claim=_claim_for(answer.rows, issued_on=availability.issue_day),
    )


def _read_release_series(  # noqa: PLR0913 - one keyword per bound the read is narrowed by
    store: ReadOnlyObjectStore,
    session: DuckDbSession,
    *,
    layer: str,
    longitude: float,
    latitude: float,
    day: date,
) -> ForecastSummary:
    """Answer from the newest published `kind=observed` ISSUE file, filtered to one UTC valid day.

    The absence of a `kind=forecast` partition is this lane's normal state, not a refusal: it
    publishes one issue per day and carries its future inside that issue as `valid_time` rows.
    """
    kind = forecast_root_kind(layer)
    availability = read_lane_availability(store, layer=layer, kind=kind, generation_days="issue_days")
    issue_day = availability.issue_day
    keys = day_part_keys(store, layer=layer, kind=kind, zoom=BASE_PARTITION_ZOOM, day=issue_day)
    newest = newest_valid_day(session, keys, valid_time_column=VALID_TIME_COLUMN)
    if newest is None:
        raise refusals.availability_no_published_day(layer=layer, kind=kind)
    published_horizon_days = max((newest - issue_day).days, 0)
    if not issue_day <= day <= newest:
        raise refusals.availability_day_not_covered(
            layer=layer,
            kind=kind,
            day=day.isoformat(),
            detail=f"issue {render_day(issue_day)} carries valid days through {render_day(newest)}",
        )
    position = _nearest(
        session,
        keys,
        layer=layer,
        day=issue_day,
        longitude=longitude,
        latitude=latitude,
        columns=POINT_POSITION_COLUMNS,
    )
    answer = rows_at_cell_position(
        session,
        keys,
        position=position,
        columns=POINT_POSITION_COLUMNS,
        valid_day=day,
        valid_time_column=VALID_TIME_COLUMN,
        row_limit=MAX_POINT_ROWS,
    )
    if not answer.rows:
        raise refusals.cell_series_absent(
            layer=layer, cell_id=f"{position.longitude},{position.latitude}", origin=issue_day.isoformat()
        )
    return ForecastSummary(
        layer=layer,
        serving_path=SERVING_PATH_RELEASE_SERIES,
        position=position,
        valid_day=day,
        rows=answer.rows,
        truncated=answer.truncated,
        availability=availability,
        published_horizon_days=published_horizon_days,
        claim=ClaimProvenance(
            artifact_sha256=None,
            artifact_absent_reason=ARTIFACT_ABSENT_NOT_MODEL_BACKED,
            issued_on=issue_day,
        ),
    )


def _nearest(  # noqa: PLR0913 - one keyword per bound the read is narrowed by
    session: DuckDbSession,
    keys: Sequence[str],
    *,
    layer: str,
    day: date,
    longitude: float,
    latitude: float,
    columns: PositionColumns = CELL_POSITION_COLUMNS,
) -> CellPosition:
    """Return the closest recorded position, or refuse rather than answering from another cell."""
    position = nearest_cell_position(session, keys, longitude=longitude, latitude=latitude, columns=columns)
    if position is None:
        raise refusals.cell_not_covered(layer=layer, day=day.isoformat(), longitude=longitude, latitude=latitude)
    return position


def _claim_for(rows: Sequence[Mapping[str, object]], *, issued_on: date) -> ClaimProvenance:
    """Return the claim block: the artifact the rows name, or the declared reason they name none.

    Three different absences, never collapsed into one. NO digest column means the lane is not
    model-backed; the sentinel means the run had no artifact to score with; TWO digests in one
    cell means the lane is model-backed and its provenance disagrees with itself, which is a fault
    an operator must see rather than a statement that no model was involved.
    """
    digests = {str(row[ARTIFACT_COLUMN]) for row in rows if row.get(ARTIFACT_COLUMN) is not None}
    row_issue = max(
        (value for value in (row.get("issued_on") for row in rows) if isinstance(value, date)), default=None
    )
    moment = row_issue if row_issue is not None else issued_on
    if len(digests) > 1:
        return ClaimProvenance(
            artifact_sha256=None, artifact_absent_reason=ARTIFACT_ABSENT_PROVENANCE_CONFLICTED, issued_on=moment
        )
    if not digests:
        return ClaimProvenance(
            artifact_sha256=None, artifact_absent_reason=ARTIFACT_ABSENT_NOT_MODEL_BACKED, issued_on=moment
        )
    resolved = next(iter(digests))
    if resolved == NO_ARTIFACT_SENTINEL:
        return ClaimProvenance(
            artifact_sha256=None, artifact_absent_reason=ARTIFACT_ABSENT_NO_ARTIFACT, issued_on=moment
        )
    return ClaimProvenance(artifact_sha256=resolved, artifact_absent_reason=None, issued_on=moment)


__all__ = [
    "ACCEPTED_DETERMINISTIC_QUANTILE",
    "ARTIFACT_COLUMN",
    "VALID_TIME_COLUMN",
    "ForecastSummary",
    "read_forecast_summary",
    "serving_path_for",
]
