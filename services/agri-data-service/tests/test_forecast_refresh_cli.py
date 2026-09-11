"""The MV refresh runs as the owner credential and reports the row count it produced.

Until ``20260808_0019`` this command probed a catalog eligibility statement and then
``SET LOCAL ROLE plantgeo_forecast_mv_refresher``. That role is retired: the matview and its
refresher function belong to the owner credential that calls them, so the refresh is now an
ordinary owner statement and the assertions below pin the *absence* of the role ceremony as
much as the presence of the refresh.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

import agri_data_service.interface.cli.commands as cli_module
from agri_data_service.execution.vegetation_ndvi_forecast import (
    PURPOSE_FORWARD_SIMULATION,
    SimulationRequest,
)

_EXPECTED_ROW_COUNT = 11
_SIMULATION_REQUEST = SimulationRequest(horizon_days=30, simulation_count=100, seed=0)


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value


class _Session:
    def __init__(self) -> None:
        self.statements: list[str] = []

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield

    async def execute(self, statement: object) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "count(*)" in sql:
            return _Result(_EXPECTED_ROW_COUNT)
        return _Result(None)


def _session_factory(session: _Session) -> Any:
    @asynccontextmanager
    async def factory(_database_url: str) -> AsyncIterator[_Session]:
        yield session

    return factory


@pytest.mark.asyncio
async def test_refresh_runs_as_the_calling_credential_and_reports_row_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    monkeypatch.setattr(
        type(cli_module.settings),
        "require_forecast_mv_refresh_database_url",
        lambda _self: "postgresql+asyncpg://operator:secret@db:5432/plantgeo",
    )
    monkeypatch.setattr(cli_module, "forecast_mv_refresh_session", _session_factory(session))

    row_count = await cli_module._forecast_refresh_ml_daily()

    assert row_count == _EXPECTED_ROW_COUNT
    assert session.statements == [
        "SET LOCAL statement_timeout = '120s'",
        "SELECT agri.refresh_forecast_ml_daily_serving()",
        "SELECT count(*) FROM agri.mv_forecast_ml_daily_serving",
    ]
    assert not any("SET LOCAL ROLE" in sql for sql in session.statements)
    assert not any("plantgeo_forecast_mv_refresher" in sql for sql in session.statements)


@pytest.mark.asyncio
async def test_refresh_propagates_a_database_failure_without_reporting_a_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed refresh must surface, never fall through to the row-count report."""

    class _FailingSession(_Session):
        async def execute(self, statement: object) -> _Result:
            sql = str(statement)
            self.statements.append(sql)
            if "refresh_forecast_ml_daily_serving" in sql:
                raise RuntimeError("refresh exploded")
            return _Result(None)

    session = _FailingSession()
    monkeypatch.setattr(
        type(cli_module.settings),
        "require_forecast_mv_refresh_database_url",
        lambda _self: "postgresql+asyncpg://operator:secret@db:5432/plantgeo",
    )
    monkeypatch.setattr(cli_module, "forecast_mv_refresh_session", _session_factory(session))

    with pytest.raises(RuntimeError, match="refresh exploded"):
        await cli_module._forecast_refresh_ml_daily()

    assert not any("count(*)" in sql for sql in session.statements)


@pytest.mark.asyncio
async def test_vegetation_simulation_validates_cutoff_and_as_of_before_resolving_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_dsn(_self: object) -> str:
        raise AssertionError("DSN resolved before simulation input validation")

    monkeypatch.setattr(type(cli_module.settings), "require_forecast_iteration_database_url", unexpected_dsn)

    with pytest.raises(ValueError, match="simulation cutoff day cannot follow"):
        await cli_module._forecast_vegetation_simulate(
            cutoff_day=date(2026, 7, 2),
            release_cutoff_day=date(2026, 7, 1),
            request=_SIMULATION_REQUEST,
            purpose=PURPOSE_FORWARD_SIMULATION,
            cell_keys=(),
            as_of_time=None,
        )

    with pytest.raises(ValueError, match="cannot be in the future"):
        await cli_module._forecast_vegetation_simulate(
            cutoff_day=date(2026, 7, 1),
            release_cutoff_day=date(2026, 7, 1),
            request=_SIMULATION_REQUEST,
            purpose=PURPOSE_FORWARD_SIMULATION,
            cell_keys=(),
            as_of_time=datetime.now(tz=UTC) + timedelta(days=1),
        )


@pytest.mark.asyncio
async def test_vegetation_evaluation_validates_cutoff_and_as_of_before_resolving_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_dsn(_self: object) -> str:
        raise AssertionError("DSN resolved before evaluation input validation")

    monkeypatch.setattr(type(cli_module.settings), "require_forecast_iteration_database_url", unexpected_dsn)

    with pytest.raises(ValueError, match="every holdout cutoff day must precede"):
        await cli_module._forecast_vegetation_evaluate(
            release_cutoff_day=date(2026, 7, 1),
            holdout_cutoff_days=(date(2026, 7, 1),),
            request=_SIMULATION_REQUEST,
            cell_keys=(),
            as_of_time=None,
        )

    with pytest.raises(ValueError, match="cannot be in the future"):
        await cli_module._forecast_vegetation_evaluate(
            release_cutoff_day=date(2026, 7, 2),
            holdout_cutoff_days=(date(2026, 7, 1),),
            request=_SIMULATION_REQUEST,
            cell_keys=(),
            as_of_time=datetime.now(tz=UTC) + timedelta(days=1),
        )
