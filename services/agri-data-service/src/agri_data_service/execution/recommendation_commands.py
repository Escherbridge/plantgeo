"""The `recommendation-*` CLI verbs: each prints one JSON summary and writes only when asked."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click

from agri_data_service.db.engine import async_session
from agri_data_service.execution.recommendation_lane import (
    load_covariate_vectors,
    load_declared_gaps,
    load_labels,
    map_labels_to_training_instances,
    release_summary,
    train_recommendation_model,
)
from agri_data_service.jobs.lease import apply_statement_timeout
from agri_data_service.method.ml.covariates_v2 import (
    SCHEMA_VERSION_V1,
    SUPPORTED_SCHEMA_VERSIONS,
    CovariateReadError,
)
from agri_data_service.method.ml.expert_label_plane import (
    ExpertLabelPlaneError,
    load_harvest_document,
)
from agri_data_service.method.ml.recommendation_models import RecommendationTrainingError

if TYPE_CHECKING:
    from collections.abc import Mapping

_LANE_ERRORS = (ExpertLabelPlaneError, RecommendationTrainingError, CovariateReadError)


def _echo(payload: Mapping[str, object]) -> None:
    """One JSON line per verb, sorted so two runs of the same command diff cleanly."""
    click.echo(json.dumps(payload, sort_keys=True, default=str))


def _require_aware(value: datetime | None, *, name: str) -> datetime:
    if value is None:
        raise click.ClickException(f"--{name} is required")
    parsed = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def register_recommendation_commands(cli: click.Group) -> None:
    """Attach the recommendation lane's verbs to the shared CLI group."""

    @cli.command("recommendation-labels-load")
    @click.option(
        "--harvest-document",
        type=click.Path(path_type=Path, exists=True, dir_okay=False, readable=True),
        required=True,
        help="The harvest JSON produced by the multi-agent literature workflow.",
    )
    @click.option("--persist", is_flag=True, default=False, help="Write the labels; off by default.")
    def recommendation_labels_load(harvest_document: Path, persist: bool) -> None:
        """Load harvested literature labels as draft, then advance the non-refuted ones."""
        _echo(asyncio.run(_load_labels(harvest_document, persist=persist)))

    @cli.command("recommendation-labels-summary")
    @click.option("--release-key", required=True)
    def recommendation_labels_summary(release_key: str) -> None:
        """Print the per-slice, per-state, per-outcome accounting of one label release."""
        _echo(asyncio.run(_release_summary(release_key)))

    @cli.command("recommendation-labels-map")
    @click.option("--release-key", required=True)
    @click.option("--label-kind", type=click.Choice(["species_fit", "strategy_outcome"]), required=True)
    @click.option("--cell-id", required=True, help="The governed spatial cell to evaluate envelopes against.")
    @click.option("--window-start", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
    @click.option("--window-end", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
    @click.option("--as-of", type=click.DateTime(), required=True, help="Knowledge horizon (UTC).")
    @click.option(
        "--schema-version",
        type=click.Choice(list(SUPPORTED_SCHEMA_VERSIONS)),
        default=SCHEMA_VERSION_V1,
        show_default=True,
    )
    @click.option("--persist", is_flag=True, default=False)
    def recommendation_labels_map(  # noqa: PLR0913 - one option per pinned input
        release_key: str,
        label_kind: str,
        cell_id: str,
        window_start: datetime,
        window_end: datetime,
        as_of: datetime,
        schema_version: str,
        persist: bool,
    ) -> None:
        """Intersect each label's condition envelope with the governed streams for one cell."""
        _echo(
            asyncio.run(
                _map_labels(
                    release_key=release_key,
                    label_kind=label_kind,
                    cell_id=cell_id,
                    window_start=window_start.date(),
                    window_end=window_end.date(),
                    as_of_time=_require_aware(as_of, name="as-of"),
                    schema_version=schema_version,
                    persist=persist,
                )
            )
        )

    @cli.command("recommendation-train")
    @click.option("--model", type=click.Choice(["species_fit", "strategy_selection"]), required=True)
    @click.option("--release-key", required=True)
    @click.option(
        "--schema-version",
        type=click.Choice(list(SUPPORTED_SCHEMA_VERSIONS)),
        default=SCHEMA_VERSION_V1,
        show_default=True,
    )
    @click.option("--persist", is_flag=True, default=False)
    def recommendation_train(model: str, release_key: str, schema_version: str, persist: bool) -> None:
        """Fit and cross-validate one recommendation model over agent-reviewed labels."""
        _echo(
            asyncio.run(
                _train(
                    model_kind=model,
                    release_key=release_key,
                    schema_version=schema_version,
                    persist=persist,
                )
            )
        )

    @cli.command("recommendation-covariate-coverage")
    @click.option("--cell-id", required=True)
    @click.option("--window-start", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
    @click.option("--window-end", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
    @click.option("--as-of", type=click.DateTime(), required=True)
    @click.option(
        "--schema-version",
        type=click.Choice(list(SUPPORTED_SCHEMA_VERSIONS)),
        default=SCHEMA_VERSION_V1,
        show_default=True,
    )
    def recommendation_covariate_coverage(
        cell_id: str, window_start: datetime, window_end: datetime, as_of: datetime, schema_version: str
    ) -> None:
        """Rebuild the covariate plane for a cell and report what each feature block covered."""
        _echo(
            asyncio.run(
                _covariate_coverage(
                    cell_id=cell_id,
                    window_start=window_start.date(),
                    window_end=window_end.date(),
                    as_of_time=_require_aware(as_of, name="as-of"),
                    schema_version=schema_version,
                )
            )
        )


async def _load_labels(harvest_document: Path, *, persist: bool) -> dict[str, object]:
    try:
        document, checksum = load_harvest_document(harvest_document)
    except (OSError, ValueError, ExpertLabelPlaneError) as exc:
        raise click.ClickException(str(exc)) from exc
    async with async_session() as session:
        await apply_statement_timeout(session)
        try:
            report = await load_labels(
                session,
                document,
                harvest_document_checksum=checksum,
                harvest_document_uri=harvest_document.as_posix(),
                persist=persist,
            )
        except _LANE_ERRORS as exc:
            raise click.ClickException(str(exc)) from exc
        if persist:
            await session.commit()
    return report.to_summary()


async def _release_summary(release_key: str) -> dict[str, object]:
    async with async_session() as session:
        await apply_statement_timeout(session)
        rows = await release_summary(session, release_key=release_key)
    return {"release_key": release_key, "rows": [dict(row) for row in rows]}


async def _map_labels(  # noqa: PLR0913 - one parameter per pinned input
    *,
    release_key: str,
    label_kind: str,
    cell_id: str,
    window_start: date,
    window_end: date,
    as_of_time: datetime,
    schema_version: str,
    persist: bool,
) -> dict[str, object]:
    async with async_session() as session:
        await apply_statement_timeout(session)
        try:
            report = await map_labels_to_training_instances(
                session,
                release_key=release_key,
                label_kind=label_kind,  # type: ignore[arg-type]
                cell_id=cell_id,
                window_start=window_start,
                window_end=window_end,
                as_of_time=as_of_time,
                schema_version=schema_version,
                persist=persist,
            )
        except _LANE_ERRORS as exc:
            raise click.ClickException(str(exc)) from exc
        if persist:
            await session.commit()
    return report.to_summary()


async def _train(*, model_kind: str, release_key: str, schema_version: str, persist: bool) -> dict[str, object]:
    async with async_session() as session:
        await apply_statement_timeout(session)
        try:
            report = await train_recommendation_model(
                session,
                model_kind=model_kind,  # type: ignore[arg-type]
                release_key=release_key,
                feature_schema_version=schema_version,
                persist=persist,
            )
        except _LANE_ERRORS as exc:
            raise click.ClickException(str(exc)) from exc
        if persist:
            await session.commit()
    return report.to_summary()


async def _covariate_coverage(
    *, cell_id: str, window_start: date, window_end: date, as_of_time: datetime, schema_version: str
) -> dict[str, object]:
    async with async_session() as session:
        await apply_statement_timeout(session)
        try:
            _vectors, coverage = await load_covariate_vectors(
                session,
                cell_id=cell_id,
                window_start=window_start,
                window_end=window_end,
                as_of_time=as_of_time,
                schema_version=schema_version,
            )
            gaps = await load_declared_gaps(session, schema_version=schema_version)
        except _LANE_ERRORS as exc:
            raise click.ClickException(str(exc)) from exc
    return {
        "cell_id": cell_id,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "as_of_time": as_of_time.isoformat(),
        "coverage": coverage.to_summary(),
        "declared_gaps": [dict(gap) for gap in gaps],
    }
