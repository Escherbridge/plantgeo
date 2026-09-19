"""The bounded DuckDB session: its guards, its refusals, and the credential it never renders twice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import duckdb
import pytest

from plantgeo_ml_service.config import Settings
from plantgeo_ml_service.pipeline.duckdb_session import (
    MAX_READ_KEYS,
    REQUIRED_EXTENSIONS,
    SPILLING_DISABLED,
    DuckDbExtensionError,
    DuckDbSession,
    DuckDbSessionError,
    load_extensions,
    open_guarded_connection,
    read_parquet_keys,
    sql_literal,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(slots=True)
class _RecordingConnection:
    """A connection that records the statement and parameters it was asked to run."""

    statements: list[tuple[str, Any]]
    result: Any = None

    def execute(self, statement: str, parameters: Any = None) -> _RecordingConnection:
        """Record one execution and return self, the shape DuckDB's own API returns."""
        self.statements.append((statement, parameters))
        return self

    def arrow(self) -> Any:
        """Return whatever this stub was primed with, standing in for the read."""
        return self.result


def _session(result: Any = None) -> DuckDbSession:
    return DuckDbSession(connection=_RecordingConnection(statements=[], result=result), bucket_uri="s3://bucket/root")


def test_spilling_is_pinned_to_zero_not_merely_small() -> None:
    """With spilling on, an over-budget query eats the disk instead of raising in about a second."""
    assert SPILLING_DISABLED == "0GiB"


def test_a_guarded_connection_refuses_to_open_without_its_extensions(tmp_path: Path) -> None:
    """The directory is real but empty, so LOAD fails rather than fetching from the network."""
    settings = Settings(duckdb_memory_limit="256MB", duckdb_thread_count=1, duckdb_extension_directory=str(tmp_path))

    with pytest.raises(DuckDbExtensionError):
        open_guarded_connection(settings=settings)


def test_a_missing_extension_is_a_named_refusal_not_a_silent_download(tmp_path: Path) -> None:
    connection = duckdb.connect()
    try:
        with pytest.raises(DuckDbExtensionError) as refusal:
            load_extensions(connection, directory=str(tmp_path))
    finally:
        connection.close()

    assert REQUIRED_EXTENSIONS[0] in str(refusal.value)


@pytest.mark.parametrize("memory_limit", ["", "2 GB", "lots", "2GBB"])
def test_a_memory_ceiling_duckdb_cannot_parse_is_refused_at_config_time(memory_limit: str) -> None:
    with pytest.raises(ValueError, match="DUCKDB_MEMORY_LIMIT"):
        Settings(duckdb_memory_limit=memory_limit)


@pytest.mark.parametrize("thread_count", [0, -1, 9])
def test_a_thread_count_outside_the_bounded_budget_is_refused(thread_count: int) -> None:
    with pytest.raises(ValueError, match="DUCKDB_THREAD_COUNT"):
        Settings(duckdb_thread_count=thread_count)


def test_an_object_uri_is_the_bucket_root_plus_the_relative_key() -> None:
    session = _session()

    assert session.object_uri("layer=signal/kind=observed/x.parquet") == (
        "s3://bucket/root/layer=signal/kind=observed/x.parquet"
    )


def test_a_read_names_its_keys_explicitly_and_never_a_glob() -> None:
    session = _session(result="table")

    answer = read_parquet_keys(session, ["layer=signal/a.parquet", "layer=signal/b.parquet"])

    statement, parameters = session.connection.statements[0]  # type: ignore[attr-defined]
    assert answer == "table"
    assert "read_parquet(?)" in statement
    assert parameters == [["s3://bucket/root/layer=signal/a.parquet", "s3://bucket/root/layer=signal/b.parquet"]]


def test_a_projection_is_identifier_quoted_so_a_column_name_cannot_end_the_statement() -> None:
    session = _session(result="table")

    read_parquet_keys(session, ["layer=signal/a.parquet"], columns=['weird"name', "observed_day"])

    statement, _parameters = session.connection.statements[0]  # type: ignore[attr-defined]
    assert 'SELECT "weird""name", "observed_day" FROM' in statement


def test_an_empty_read_is_a_caller_bug_not_an_answer() -> None:
    with pytest.raises(DuckDbSessionError):
        read_parquet_keys(_session(), [])


def test_a_read_past_the_key_budget_is_refused() -> None:
    keys = [f"layer=signal/part-{index}.parquet" for index in range(MAX_READ_KEYS + 1)]

    with pytest.raises(DuckDbSessionError):
        read_parquet_keys(_session(), keys)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("plain", "'plain'"), ("it's", "'it''s'"), ("'; DROP TABLE x; --", "'''; DROP TABLE x; --'")],
)
def test_a_sql_literal_cannot_end_its_own_statement(value: str, expected: str) -> None:
    assert sql_literal(value) == expected
