"""Reconciler cutover, checkpoint locking, atomic writes, and resume semantics."""

from __future__ import annotations

import importlib.util
import math
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from filelock import FileLock

from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _tool() -> Any:
    path = Path(__file__).parents[2] / "scripts" / "reconcile_fire_detections_parquet.py"
    name = "plantgeo_fire_detections_reconcile_tool"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TOOL = _tool()


def test_reconciler_defaults_to_writer_ceiling_and_rejects_newer_explicit_day() -> None:
    lane = LANE_REGISTRY["fire-detections"]
    default = TOOL._parser().parse_args(["--today", "2026-09-01"])

    assert TOOL._resolve_audit_window(default, lane)[1].isoformat() == "2026-08-24"

    explicit = TOOL._parser().parse_args(["--today", "2026-09-01", "--through", "2026-08-25"])
    with pytest.raises(ValueError, match="exceeds the generic writer ceiling"):
        TOOL._resolve_audit_window(explicit, lane)


def test_residual_lock_file_does_not_block_restart_but_live_holder_does(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    lock_path = checkpoint.with_suffix(".json.lock")
    lock_path.write_text("residual from a dead process\n", encoding="utf-8")

    with TOOL._checkpoint_run_lock(checkpoint, attempts=1, poll_seconds=0):
        pass

    active = FileLock(lock_path)
    with (
        active.acquire(timeout=0),
        pytest.raises(RuntimeError, match="another live run"),
        TOOL._checkpoint_run_lock(checkpoint, attempts=1, poll_seconds=0),
    ):
        pass


@pytest.mark.asyncio
async def test_run_refuses_live_checkpoint_holder_before_running_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    args = TOOL._parser().parse_args(
        [
            "--checkpoint",
            str(checkpoint),
            "--checkpoint-lock-attempts",
            "1",
            "--checkpoint-lock-poll-seconds",
            "0",
        ]
    )
    called = False

    async def run_locked(*_args: object, **_kwargs: object) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(TOOL, "_run_locked", run_locked)
    with (
        FileLock(checkpoint.with_suffix(".json.lock")).acquire(timeout=0),
        pytest.raises(RuntimeError, match="another live run"),
    ):
        await TOOL._run(args)

    assert called is False


def test_atomic_json_uses_unique_temp_and_cleans_it_after_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "checkpoint.json"
    target.write_text("old\n", encoding="utf-8")
    seen: list[Path] = []

    def explode(source: object, destination: object) -> None:
        seen.append(Path(source))
        assert Path(destination) == target
        raise OSError("replace failed")

    monkeypatch.setattr(TOOL.os, "replace", explode)
    with pytest.raises(OSError, match="replace failed"):
        TOOL._atomic_json(target, {"new": True})

    assert target.read_text(encoding="utf-8") == "old\n"
    assert len(seen) == 1
    assert seen[0].parent == target.parent
    assert seen[0].name.startswith(f".{target.name}.")
    assert not seen[0].exists()


@pytest.mark.parametrize(
    "field",
    [
        "r2_base_delay_seconds",
        "postgres_read_base_delay_seconds",
        "lock_poll_seconds",
        "checkpoint_lock_poll_seconds",
        "repair_delay_seconds",
    ],
)
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_reconciler_rejects_nonfinite_waits(field: str, value: float) -> None:
    args = TOOL._parser().parse_args([])
    setattr(args, field, value)

    with pytest.raises(SystemExit, match="bounded finite delay"):
        TOOL._validate_args(args)


def test_reconciler_rejects_wait_and_attempt_caps() -> None:
    args = TOOL._parser().parse_args([])
    args.lock_poll_seconds = TOOL.MAX_POLL_DELAY_SECONDS + 1
    with pytest.raises(SystemExit, match="bounded finite delay"):
        TOOL._validate_args(args)

    args = TOOL._parser().parse_args([])
    args.r2_attempts = TOOL.MAX_RETRY_ATTEMPTS + 1
    with pytest.raises(SystemExit, match="bounded positive integer"):
        TOOL._validate_args(args)


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_parity", [True, False])
async def test_every_saved_month_is_fully_reaudited(
    saved_parity: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = TOOL.date(2026, 8, 1)
    checkpoint_path = tmp_path / "checkpoint.json"
    manifest_path = tmp_path / "manifest.json"
    args = TOOL._parser().parse_args(
        [
            "--first-day",
            day.isoformat(),
            "--through",
            day.isoformat(),
            "--today",
            "2026-08-10",
            "--checkpoint",
            str(checkpoint_path),
            "--manifest",
            str(manifest_path),
        ]
    )
    checkpoint: dict[str, object] = {
        "schema_version": TOOL.CHECKPOINT_SCHEMA_VERSION,
        "stream": "fire-detections",
        "kind": "observed",
        "tiers": list(TOOL.TIERS),
        "first_day": day.isoformat(),
        "last_day": day.isoformat(),
        "months": {"2026-08": {"parity": saved_parity, "stale": True}},
    }
    audits: list[tuple[object, object]] = []
    writes: list[tuple[Path, dict[str, object]]] = []

    class Result:
        def scalar_one_or_none(self) -> str:
            return "layer-id"

    class Session:
        async def execute(self, *_args: object, **_kwargs: object) -> Result:
            return Result()

        async def rollback(self) -> None:
            return None

    @asynccontextmanager
    async def loader_session(_database_url: str) -> AsyncIterator[Session]:
        yield Session()

    async def audit(
        _session: object, _store: object, *, first_day: object, last_day: object, **_kwargs: object
    ) -> tuple[dict[str, object], set[object], set[object]]:
        audits.append((first_day, last_day))
        return (
            {
                "month": "2026-08",
                "calendar_days": 1,
                "source_postgres": {"data_days": 1},
                "issues": [],
                "parity": True,
            },
            set(),
            set(),
        )

    def manifest(current: dict[str, object], **_kwargs: object) -> dict[str, object]:
        months = current["months"]
        assert isinstance(months, dict)
        saved = months["2026-08"]
        assert isinstance(saved, dict)
        assert saved.get("stale") is None
        return {
            "parity": True,
            "first_day": day.isoformat(),
            "last_day": day.isoformat(),
            "source_postgres": {
                "data_days": 1,
                "governed_absence_days": 0,
                "detection_count": 1,
            },
            "issue_count": 0,
        }

    def atomic(path: Path, payload: dict[str, object]) -> None:
        writes.append((path, dict(payload)))

    monkeypatch.setattr(TOOL, "_load_checkpoint", lambda *_args, **_kwargs: checkpoint)
    monkeypatch.setattr(TOOL, "_audit_month", audit)
    monkeypatch.setattr(TOOL, "_final_manifest", manifest)
    monkeypatch.setattr(TOOL, "_atomic_json", atomic)
    monkeypatch.setattr(TOOL, "local_source_loader_session", loader_session)
    monkeypatch.setattr(
        TOOL,
        "settings",
        SimpleNamespace(require_local_source_loader_database_url=lambda: "db"),
    )
    monkeypatch.setattr(
        TOOL.ObjectStore,
        "from_settings",
        classmethod(lambda _cls, _settings=None: object()),
    )

    result = await TOOL._run_locked(args, checkpoint_path=checkpoint_path)

    assert result == 0
    assert audits == [(day, day)]
    final = writes[-1][1]
    assert final["checkpoint_revalidation"] == {
        "strategy": "full_source_and_r2_reaudit",
        "saved_months_revalidated": 1,
        "saved_parity_months_revalidated": int(saved_parity),
        "skipped_months": 0,
    }
