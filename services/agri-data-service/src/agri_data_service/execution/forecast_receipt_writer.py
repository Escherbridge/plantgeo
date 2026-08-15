"""Shared evaluation-only forecast training/backtest receipt-chain writer.

`covariate_wind_persist.py` and `analog_ensemble_persist.py` persist an IDENTICAL receipt chain
-- the same eleven governed SQL files (`select_validated_release_set.sql` through
`insert_forecast_backtest_metric.sql` and `validate_training_run.sql`), the same
insert-or-resolve idempotency shape, and the same release-set/quality-policy resolution -- because
both are `model_kind = 'ml'` forecasters writing the same warehouse contracts. This module is the
ONE `load_query_sql` call site for each of those files and owns the four helpers both lanes drove
identically, so neither lane forks a byte-identical copy. See `execution/AGENTS.md` under
`covariate_wind_persist.py` for what a persisted receipt writes and what it deliberately never
writes. Lane-specific parameter shaping, model documents, and the exception TYPE a failure
surfaces as stay in each lane's own module; the helpers below take that type as `error_type`
rather than importing either lane, which would invert the dependency this module exists to break.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause

SELECT_RELEASE_SET: Final = text(load_query_sql("execution/select_validated_release_set.sql"))
INSERT_JOB_DEFINITION: Final = text(load_query_sql("execution/insert_training_job_definition.sql"))
INSERT_JOB_RUN: Final = text(load_query_sql("execution/insert_training_job_run.sql"))
INSERT_MODEL_ARTIFACT: Final = text(load_query_sql("execution/insert_model_artifact.sql"))
INSERT_JOB_OUTPUT: Final = text(load_query_sql("execution/insert_job_output.sql"))
INSERT_FEATURE_SNAPSHOT: Final = text(load_query_sql("execution/insert_forecast_feature_snapshot.sql"))
INSERT_FORECAST_MODEL: Final = text(load_query_sql("execution/insert_forecast_model.sql"))
INSERT_TRAINING_RUN: Final = text(load_query_sql("execution/insert_forecast_training_run.sql"))
INSERT_FORECAST_RUN: Final = text(load_query_sql("execution/insert_forecast_run.sql"))
INSERT_BACKTEST_METRIC: Final = text(load_query_sql("execution/insert_forecast_backtest_metric.sql"))
VALIDATE_TRAINING_RUN: Final = text(load_query_sql("execution/validate_training_run.sql"))

# Single-line, not file-backed -- outside the sql/ tree's "one canonical file" convention on
# purpose (see code_styleguides/sql.md, "where inline SQL still belongs"). Owned here because
# `resolve_quality_policy` below is its only caller in either lane.
_SELECT_QUALITY_POLICY: Final = text(
    "SELECT id FROM agri.forecast_quality_policy WHERE policy_key = :policy_key AND is_active"
)


class ForecastReceiptPersistError(RuntimeError):
    """Base class for a lane's own persist-error; every raise below takes the lane's subclass in."""


async def scalar_uuid(
    session: AsyncSession, statement: TextClause, parameters: Mapping[str, object]
) -> uuid.UUID | None:
    """Run a statement expected to yield at most one id, returning it as a UUID."""
    value = (await session.execute(statement, dict(parameters))).scalar_one_or_none()
    return None if value is None else uuid.UUID(str(value))


async def insert_or_resolve(  # noqa: PLR0913 - one parameter per statement/parameter pair, plus the label
    session: AsyncSession,
    *,
    insert_statement: TextClause,
    insert_parameters: Mapping[str, object],
    lookup_statement: TextClause,
    lookup_parameters: Mapping[str, object],
    description: str,
    error_type: type[ForecastReceiptPersistError],
) -> uuid.UUID:
    """Insert a row keyed by a derived identity, or resolve the one a previous attempt already wrote.

    Every insert in this chain is `ON CONFLICT ... DO NOTHING RETURNING id`, which yields no row
    on the conflict path. That is what makes a re-claimed durable shard idempotent instead of a
    unique-violation dead letter. `error_type` is the calling lane's own persist-error subclass,
    so a caller's `except`/`pytest.raises` keeps naming the lane it means.
    """
    inserted = await scalar_uuid(session, insert_statement, insert_parameters)
    if inserted is not None:
        return inserted
    existing = await scalar_uuid(session, lookup_statement, lookup_parameters)
    if existing is None:
        raise error_type(f"{description} was neither inserted nor found")
    return existing


@dataclass(frozen=True, slots=True)
class ReleaseBinding:
    """The governed release set a receipt pins its inputs to."""

    release_set_id: uuid.UUID
    logical_key: str
    manifest_checksum: str


async def resolve_release_set(
    session: AsyncSession, *, as_of_time: datetime, error_type: type[ForecastReceiptPersistError]
) -> ReleaseBinding:
    """Bind the newest validated release set that already existed at the run's as-of instant."""
    row = (await session.execute(SELECT_RELEASE_SET, {"as_of_time": as_of_time})).mappings().first()
    if row is None:
        raise error_type(
            "no validated release set exists at or before the as-of instant; a training receipt "
            "cannot pin its inputs, and this lane will not mint a release set of its own"
        )
    return ReleaseBinding(
        release_set_id=uuid.UUID(str(row["id"])),
        logical_key=str(row["logical_key"]),
        manifest_checksum=str(row["manifest_checksum"]),
    )


async def resolve_quality_policy(
    session: AsyncSession, *, policy_key: str, error_type: type[ForecastReceiptPersistError]
) -> uuid.UUID:
    """Resolve the reviewed quality policy a forecast run must reference; never mint one."""
    policy_id = await scalar_uuid(session, _SELECT_QUALITY_POLICY, {"policy_key": policy_key})
    if policy_id is None:
        raise error_type(
            f"no active agri.forecast_quality_policy with policy_key {policy_key!r}. A quality policy "
            "encodes the thresholds a forecast must clear, so this lane will not invent one; seed it "
            "through the reviewed path first"
        )
    return policy_id
