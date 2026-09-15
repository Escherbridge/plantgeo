"""The availability CLI validates a pinned document offline and reaches the network only for --apply."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

import agri_data_service.interface.cli.availability as availability_cli
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.interface.cli import cli
from tests.parquet.availability_documents import (
    CEILING,
    LANE_ROOT,
    TRUSTED_DAY,
    MemoryAvailabilityStorage,
    bootstrap_document,
    bootstrap_request,
    digested_day_rows,
    publication_document,
    write_document,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path


def _pinned_bootstrap(tmp_path: Path) -> tuple[Path, str, int]:
    request = bootstrap_request(MemoryAvailabilityStorage())
    document = bootstrap_document(request, rows=[row.to_wire() for row in request.rows])
    path = write_document(tmp_path, document)
    return path, sha256_digest(path.read_bytes()), len(request.rows)


def _pinned_publication(tmp_path: Path) -> tuple[Path, str, int]:
    # A forward publication may not carry a manifest-trusted row, so publish the DIGESTED day.
    store = MemoryAvailabilityStorage()
    request = bootstrap_request(store)
    rows = digested_day_rows(store, request.identity, TRUSTED_DAY)
    document = publication_document(request, rows=[row.to_wire() for row in rows])
    path = write_document(tmp_path, document, name="publication.json")
    return path, sha256_digest(path.read_bytes()), len(rows)


def _forbid_live_dependencies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every live dependency construction observable, and fatal, unless a test wants it."""
    touched: list[str] = []

    def refuse(name: str):  # noqa: ANN202 - a test double standing in for two unrelated callables
        def constructed(*_args: object, **_kwargs: object) -> object:
            touched.append(name)
            raise AssertionError(f"{name} must never be constructed without --apply")

        return constructed

    monkeypatch.setattr(availability_cli, "_storage", refuse("object store"))
    monkeypatch.setattr(availability_cli, "receiver_writer_session", refuse("writer session"))
    return touched


def test_bootstrap_validates_offline_and_constructs_no_live_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE SAFETY PROPERTY: a perfectly valid document still touches nothing without --apply."""
    path, digest, rows = _pinned_bootstrap(tmp_path)
    touched = _forbid_live_dependencies(monkeypatch)

    result = CliRunner().invoke(
        cli,
        [
            "data",
            "availability-bootstrap",
            "--input",
            str(path),
            "--expected-sha256",
            digest,
            "--expected-row-count",
            str(rows),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied"] is False
    assert payload["operation"] == "availability-bootstrap"
    assert payload["request"]["lane_root"] == LANE_ROOT
    assert payload["request"]["rows"] == rows
    assert payload["request"]["input_sha256"] == digest
    assert payload["request"]["source_ceiling"] == CEILING.isoformat()
    assert payload["request"]["input_receipts"]
    assert not touched


def test_publish_validates_offline_and_constructs_no_live_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The append/correction verb holds the same offline-by-default contract."""
    path, digest, rows = _pinned_publication(tmp_path)
    touched = _forbid_live_dependencies(monkeypatch)

    result = CliRunner().invoke(
        cli,
        [
            "data",
            "availability-publish",
            "--input",
            str(path),
            "--expected-sha256",
            digest,
            "--expected-row-count",
            str(rows),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied"] is False
    assert payload["request"]["rows"] == rows
    assert payload["request"]["bootstrap_receipt"]["sha256"] == "a" * 64
    assert not touched


@pytest.mark.parametrize("verb", ["availability-bootstrap", "availability-publish"])
def test_a_document_that_misses_its_pin_is_refused_before_anything_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
) -> None:
    """An externally pinned digest that does not match the bytes ends the command at the loader."""
    _path, _digest, rows = _pinned_bootstrap(tmp_path)
    touched = _forbid_live_dependencies(monkeypatch)

    result = CliRunner().invoke(
        cli,
        [
            "data",
            verb,
            "--input",
            str(_path),
            "--expected-sha256",
            "f" * 64,
            "--expected-row-count",
            str(rows),
            "--apply",
        ],
    )

    assert result.exit_code != 0
    assert "invalid_availability_input" in result.output
    assert not touched


def test_a_malformed_document_is_refused_with_a_typed_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document missing a required field never reaches the publisher, even with --apply."""
    request = bootstrap_request(MemoryAvailabilityStorage())
    document = bootstrap_document(request, rows=[row.to_wire() for row in request.rows])
    del document["input_receipts"]
    path = write_document(tmp_path, document)
    touched = _forbid_live_dependencies(monkeypatch)

    result = CliRunner().invoke(
        cli,
        [
            "data",
            "availability-bootstrap",
            "--input",
            str(path),
            "--expected-sha256",
            sha256_digest(path.read_bytes()),
            "--expected-row-count",
            str(len(request.rows)),
            "--apply",
        ],
    )

    assert result.exit_code != 0
    assert "invalid_availability_input" in result.output
    assert not touched


def test_apply_publishes_through_the_contract_and_reports_its_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With --apply the adapter builds the store and session and returns the winning pointer."""
    path, digest, rows = _pinned_bootstrap(tmp_path)
    published: list[object] = []

    async def publish(_session: object, _store: object, request: object) -> object:
        published.append(request)
        return _StubResult()

    monkeypatch.setattr(availability_cli, "_storage", lambda: "store")
    monkeypatch.setattr(availability_cli, "receiver_writer_session", _stub_session)
    monkeypatch.setattr(availability_cli, "bootstrap_availability", publish)

    result = CliRunner().invoke(
        cli,
        [
            "data",
            "availability-bootstrap",
            "--input",
            str(path),
            "--expected-sha256",
            digest,
            "--expected-row-count",
            str(rows),
            "--apply",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied"] is True
    assert payload["advanced"] is True
    assert payload["attempts"] == 1
    assert payload["pointer"] == {"lane_root": LANE_ROOT}
    assert len(published) == 1


class _StubPointer:
    def to_wire(self) -> dict[str, object]:
        return {"lane_root": LANE_ROOT}


class _StubResult:
    pointer = _StubPointer()
    advanced = True
    attempts = 1


@asynccontextmanager
async def _stub_writer_session() -> AsyncIterator[str]:
    """Stand in for the real writer session without opening a pool."""
    yield "session"


def _stub_session() -> AbstractAsyncContextManager[str]:
    """Match `receiver_writer_session`'s zero-argument call shape."""
    return _stub_writer_session()
