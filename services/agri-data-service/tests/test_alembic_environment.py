"""The real Alembic environment qualifies its ledger across baseline search-path changes."""

from __future__ import annotations

import runpy
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from agri_data_service import config as service_config
from agri_data_service.db.revisions import BASELINE_REVISION
from alembic import context

_ENVIRONMENT = Path(__file__).resolve().parents[1] / "alembic" / "env.py"


@pytest.mark.parametrize("offline", [False, True])
def test_environment_qualifies_ledger_creation_reads_and_stamp(monkeypatch: pytest.MonkeyPatch, offline: bool) -> None:
    configured: dict[str, Any] = {}
    connection = object()
    engine = SimpleNamespace(connect=lambda: nullcontext(connection), dispose=lambda: None)

    def capture_configuration(**options: Any) -> None:
        configured.update(options)

    monkeypatch.setattr(service_config, "settings", SimpleNamespace(database_url_sync="postgresql://127.0.0.1/qa"))
    monkeypatch.setattr(sa, "engine_from_config", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(context, "config", Config(), raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: offline)
    monkeypatch.setattr(context, "configure", capture_configuration)
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", lambda: None)

    runpy.run_path(str(_ENVIRONMENT))

    assert ("url" in configured) is offline
    assert (configured.get("connection") is connection) is (not offline)
    migration = MigrationContext.configure(dialect_name="postgresql", opts={**configured, "as_sql": offline})
    ledger = migration._version
    dialect = postgresql.dialect()
    create_sql = str(CreateTable(ledger).compile(dialect=dialect))
    read_sql = str(sa.select(ledger.c.version_num).compile(dialect=dialect))
    stamp_sql = str(ledger.insert().values(version_num=BASELINE_REVISION).compile(dialect=dialect))
    assert "CREATE TABLE public.alembic_version" in create_sql
    assert "FROM public.alembic_version" in read_sql
    assert "INSERT INTO public.alembic_version" in stamp_sql
