"""The MV refresh operator assumes only its reviewed transaction-local role."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from agri_data_service import cli as cli_module

_EXPECTED_ROW_COUNT = 11


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value

    def scalar_one(self) -> object:
        return self.value


class _Session:
    def __init__(self, *, eligible: bool) -> None:
        self.eligible = eligible
        self.statements: list[str] = []

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield

    async def execute(self, statement: object) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "pg_auth_members" in sql:
            return _Result(self.eligible)
        if "count(*)" in sql:
            return _Result(_EXPECTED_ROW_COUNT)
        return _Result(None)


def _session_factory(session: _Session) -> Any:
    @asynccontextmanager
    async def factory(_database_url: str) -> AsyncIterator[_Session]:
        yield session

    return factory


@pytest.mark.asyncio
async def test_refresh_sets_capability_role_and_reports_row_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(eligible=True)
    monkeypatch.setattr(
        type(cli_module.settings),
        "require_forecast_mv_refresh_database_url",
        lambda _self: "postgresql+asyncpg://operator:secret@db:5432/plantgeo",
    )
    monkeypatch.setattr(cli_module, "forecast_mv_refresh_session", _session_factory(session))

    row_count = await cli_module._forecast_refresh_ml_daily()

    assert row_count == _EXPECTED_ROW_COUNT
    assert "NOT role.rolinherit" in session.statements[0]
    assert "NOT membership.admin_option" in session.statements[0]
    assert "NOT membership.inherit_option" in session.statements[0]
    assert "membership.set_option" in session.statements[0]
    assert "capability.rolname <> 'plantgeo_forecast_mv_refresher'" in session.statements[0]
    assert "aclexplode" in session.statements[0]
    assert "database.datdba" in session.statements[0]
    assert "namespace.nspowner" in session.statements[0]
    assert "class.relowner" in session.statements[0]
    assert "procedure.proowner" in session.statements[0]
    assert "pg_attribute AS attribute" in session.statements[0]
    assert "attribute.attacl" in session.statements[0]
    assert any("SET LOCAL ROLE plantgeo_forecast_mv_refresher" in sql for sql in session.statements)
    assert session.statements[-2:] == [
        "SELECT agri.refresh_forecast_ml_daily_serving()",
        "SELECT count(*) FROM agri.mv_forecast_ml_daily_serving",
    ]


@pytest.mark.asyncio
async def test_refresh_rejects_login_without_reviewed_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(eligible=False)
    monkeypatch.setattr(
        type(cli_module.settings),
        "require_forecast_mv_refresh_database_url",
        lambda _self: "postgresql+asyncpg://operator:secret@db:5432/plantgeo",
    )
    monkeypatch.setattr(cli_module, "forecast_mv_refresh_session", _session_factory(session))

    with pytest.raises(ValueError, match="dedicated NOINHERIT"):
        await cli_module._forecast_refresh_ml_daily()

    assert not any("refresh_forecast_ml_daily_serving" in sql for sql in session.statements)
