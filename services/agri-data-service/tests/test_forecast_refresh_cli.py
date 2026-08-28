"""The MV refresh runs as the owner credential and reports the row count it produced.

Until ``20260808_0019`` this command probed a catalog eligibility statement and then
``SET LOCAL ROLE plantgeo_forecast_mv_refresher``. That role is retired: the matview and its
refresher function belong to the owner credential that calls them, so the refresh is now an
ordinary owner statement and the assertions below pin the *absence* of the role ceremony as
much as the presence of the refresh.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

import agri_data_service.interface.cli.commands as cli_module

_EXPECTED_ROW_COUNT = 11


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
