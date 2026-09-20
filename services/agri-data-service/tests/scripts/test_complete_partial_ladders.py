"""Safety contract for the bounded physical ladder-completion operator command."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Final

import pyarrow as pa
import pytest

from agri_data_service.foundation.parquet.completion import CompletedPart, PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    derived_empty_completion_marker_path,
    partition_path,
)
from agri_data_service.pipeline.parquet.derivation import DerivationResult
from agri_data_service.pipeline.parquet.objectstore import CompletionMarkerRead, PartitionRead, ReadPartReceipt
from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

COMMAND: Any = load_scripts_module("complete_partial_ladders.py", "complete_partial_ladders")

LAYER: Final = "sensors"
DAY: Final = date(2026, 8, 1)
NOW: Final = datetime(2026, 9, 19, tzinfo=UTC)
DIGEST: Final = "a" * 64


class FakeStore:
    """A physical object inventory with enough reads and writes to test orchestration order."""

    def __init__(self) -> None:
        self.parts: dict[int, tuple[str, ...]] = {}
        self.completions: dict[int, PartitionCompletion] = {}
        self.absences: set[int] = set()
        self.operations: list[str] = []
        self.read_digest = DIGEST
        self.marker_digest_override: str | None = None
        self.corrupt_completion_writes = False

    def add_parts(self, tier: int, *, closed: bool = False, legacy: bool = False) -> None:
        self.parts[tier] = (partition_path(LAYER, "observed", tier, DAY, 0),)
        if closed:
            self.completions[tier] = PartitionCompletion(
                part_count=1,
                row_count=1,
                completed_at=NOW,
                run_id="seed",
                parts=()
                if legacy
                else (
                    CompletedPart(relative_path=self.parts[tier][0], row_count=1, byte_count=20, sha256=DIGEST),
                ),
            )

    def list_partition_keys(
        self,
        layer: str,
        kind: PartitionKind,
        tier: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        assert (layer, kind, year, month) == (LAYER, "observed", DAY.year, DAY.month)
        keys = list(self.parts.get(tier, ()))
        if tier in self.completions:
            marker = self.completions[tier]
            path = derived_empty_completion_marker_path if marker.derived_empty else completion_marker_path
            keys.append(path(LAYER, "observed", tier, DAY))
        if tier in self.absences:
            keys.append(absence_marker_path(LAYER, "observed", tier, DAY))
        return tuple(keys)

    def read_completion_receipt(
        self, layer: str, kind: PartitionKind, tier: ZoomTier, day: date
    ) -> CompletionMarkerRead | None:
        assert (layer, kind, day) == (LAYER, "observed", DAY)
        marker = self.completions.get(tier)
        if marker is None:
            return None
        path = derived_empty_completion_marker_path if marker.derived_empty else completion_marker_path
        return CompletionMarkerRead(
            relative_path=path(LAYER, "observed", tier, DAY),
            completion=marker,
            byte_count=10,
            sha256=self.marker_digest_override or hashlib.sha256(marker.to_json_bytes()).hexdigest(),
        )

    def read_partition_with_receipts(self, layer: str, kind: PartitionKind, tier: ZoomTier, day: date) -> PartitionRead:
        assert (layer, kind, day) == (LAYER, "observed", DAY)
        return PartitionRead(
            table=pa.table({"value": [1]}),
            parts=(
                ReadPartReceipt(relative_path=self.parts[tier][0], row_count=1, byte_count=20, sha256=self.read_digest),
            ),
        )

    def write_completion_marker(
        self,
        completion: PartitionCompletion,
        *,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        day: date,
    ) -> None:
        assert (layer, kind, day) == (LAYER, "observed", DAY)
        if self.corrupt_completion_writes:
            completion = PartitionCompletion(
                part_count=completion.part_count,
                row_count=completion.row_count,
                completed_at=completion.completed_at,
                run_id=completion.run_id,
                parts=tuple(
                    CompletedPart(
                        relative_path=part.relative_path,
                        row_count=part.row_count,
                        byte_count=part.byte_count,
                        sha256="b" * 64,
                    )
                    for part in completion.parts
                ),
            )
        self.completions[zoom] = completion
        self.operations.append(f"completion:z{zoom}")


def _fake_derive(store: FakeStore, **arguments: object) -> DerivationResult:
    tiers = arguments["tiers"]
    assert isinstance(tiers, tuple)
    for tier in tiers:
        assert isinstance(tier, int)
        store.parts[tier] = (partition_path(LAYER, "observed", tier, DAY, 0),)
        store.completions[tier] = PartitionCompletion(
            part_count=1,
            row_count=1,
            completed_at=NOW,
            run_id=str(arguments["run_id"]),
            parts=(CompletedPart(relative_path=store.parts[tier][0], row_count=1, byte_count=20, sha256=DIGEST),),
        )
        store.operations.append(f"derive:z{tier}")
    return DerivationResult(tiers=(), notes=())


def _add_complete_ladder(store: FakeStore, *, legacy: bool = False) -> None:
    for tier in COMMAND.LADDER_RUNGS:
        store.add_parts(tier, closed=True, legacy=legacy)


def test_dry_run_is_default_and_reports_the_exact_missing_rungs() -> None:
    store = FakeStore()
    store.add_parts(COMMAND.BASE_RUNG, closed=True)

    receipt = asyncio.run(COMMAND.complete_range(store, layer=LAYER, first=DAY, last=DAY))

    assert receipt["dry_run"] is True
    assert receipt["applied_days"] == []
    assert receipt["refused_day_count"] == 0
    assert receipt["plans"] == [
        {
            "base_marker_missing": False,
            "day": DAY.isoformat(),
            "legacy_completion_receipts": [],
            "legacy_completion_rungs": [],
            "missing_derived_rungs": list(COMMAND.DERIVED_ZOOM_TIERS),
            "refusal": None,
            "status": "repairable",
        }
    ]
    assert store.operations == []


def test_an_unmarked_base_below_a_completed_derived_rung_is_refused() -> None:
    store = FakeStore()
    store.add_parts(COMMAND.BASE_RUNG)
    store.add_parts(COMMAND.DERIVED_ZOOM_TIERS[0], closed=True)

    plan = COMMAND.inspect_day(store, layer=LAYER, day=DAY)

    assert plan.refusal == "z13 has no completion marker while a derived rung is already complete"
    assert store.operations == []


def test_locked_apply_writes_derived_rungs_then_the_base_receipt_last() -> None:
    store = FakeStore()
    store.add_parts(COMMAND.BASE_RUNG)
    locks: list[str] = []

    @asynccontextmanager
    async def publication(_session: object, lane_root: str) -> AsyncIterator[bool]:
        locks.append(f"publication:{lane_root}")
        yield True

    @asynccontextmanager
    async def day_lock(_session: object, key: str) -> AsyncIterator[bool]:
        locks.append(f"day:{key}")
        yield True

    receipt = asyncio.run(
        COMMAND.complete_range(
            store,
            layer=LAYER,
            first=DAY,
            last=DAY,
            apply_changes=True,
            run_id="repair",
            session=object(),
            publication_barrier=publication,
            lane_day_lock=day_lock,
            now=lambda: NOW,
            derive=_fake_derive,
        )
    )

    assert locks == [
        "publication:layer=sensors/kind=observed",
        f"day:parquet-gap-fill:sensors:observed:z13:{DAY.isoformat()}",
    ]
    assert store.operations == [
        *(f"derive:z{tier}" for tier in COMMAND.DERIVED_ZOOM_TIERS),
        "completion:z13",
    ]
    assert receipt["applied_days"] == [DAY.isoformat()]
    assert store.completions[COMMAND.BASE_RUNG].parts[0].relative_path == store.parts[COMMAND.BASE_RUNG][0]


def test_legacy_count_only_ladder_is_physically_bound_and_upgraded_under_locks() -> None:
    store = FakeStore()
    _add_complete_ladder(store, legacy=True)
    reviewed = COMMAND.inspect_day(store, layer=LAYER, day=DAY)

    assert reviewed.refusal is None
    assert reviewed.legacy_completion_rungs == COMMAND.LADDER_RUNGS
    assert all(state.completion_sha256 is not None for state in reviewed.states)
    assert all(state.physical_parts[0].sha256 == DIGEST for state in reviewed.states)
    wire: Any = reviewed.to_wire()
    assert [item["rung"] for item in wire["legacy_completion_receipts"]] == list(COMMAND.LADDER_RUNGS)
    assert all(item["completion_sha256"] for item in wire["legacy_completion_receipts"])
    assert all(item["parts"][0]["sha256"] == DIGEST for item in wire["legacy_completion_receipts"])

    @asynccontextmanager
    async def publication(_session: object, _lane_root: str) -> AsyncIterator[bool]:
        yield True

    @asynccontextmanager
    async def day_lock(_session: object, _key: str) -> AsyncIterator[bool]:
        yield True

    receipt = asyncio.run(
        COMMAND.complete_range(
            store,
            layer=LAYER,
            first=DAY,
            last=DAY,
            apply_changes=True,
            run_id="legacy-upgrade",
            session=object(),
            publication_barrier=publication,
            lane_day_lock=day_lock,
            now=lambda: NOW,
        )
    )

    assert receipt["failures"] == []
    assert receipt["applied_days"] == [DAY.isoformat()]
    assert store.operations == [f"completion:z{tier}" for tier in COMMAND.LADDER_RUNGS]
    assert all(store.completions[tier].schema_version == 2 for tier in COMMAND.LADDER_RUNGS)
    assert all(store.completions[tier].run_id == "legacy-upgrade" for tier in COMMAND.LADDER_RUNGS)


def test_a_complete_v2_ladder_is_a_noop() -> None:
    store = FakeStore()
    _add_complete_ladder(store)

    receipt = asyncio.run(COMMAND.complete_range(store, layer=LAYER, first=DAY, last=DAY))

    assert receipt["plans"][0]["status"] == "complete"
    assert receipt["plans"][0]["legacy_completion_receipts"] == []
    assert receipt["plans"][0]["legacy_completion_rungs"] == []
    assert store.operations == []


@pytest.mark.parametrize("conflict", ["derived_parts", "absence"])
def test_conflicting_physical_claims_are_refused_without_writes(conflict: str) -> None:
    store = FakeStore()
    store.add_parts(COMMAND.BASE_RUNG, closed=True)
    if conflict == "derived_parts":
        store.add_parts(COMMAND.DERIVED_ZOOM_TIERS[0])
    else:
        store.absences.add(COMMAND.BASE_RUNG)

    plan = COMMAND.inspect_day(store, layer=LAYER, day=DAY)

    assert plan.refusal is not None
    assert store.operations == []


def test_apply_preflight_is_all_or_nothing_when_a_day_is_refused() -> None:
    store = FakeStore()
    store.add_parts(COMMAND.BASE_RUNG, closed=True)
    store.absences.add(COMMAND.BASE_RUNG)

    with pytest.raises(COMMAND.LadderCompletionError, match="no writes were attempted"):
        asyncio.run(COMMAND.complete_range(store, layer=LAYER, first=DAY, last=DAY, apply_changes=True))

    assert store.operations == []


def test_a_stale_completion_row_count_is_not_trusted_or_preserved() -> None:
    store = FakeStore()
    store.add_parts(COMMAND.BASE_RUNG)
    store.completions[COMMAND.BASE_RUNG] = PartitionCompletion(
        part_count=1, row_count=2, completed_at=NOW, run_id="stale"
    )

    plan = COMMAND.inspect_day(store, layer=LAYER, day=DAY)

    assert plan.refusal is not None
    assert "physical parts hold 1" in plan.refusal
    assert store.operations == []


def test_a_completion_bound_to_different_part_bytes_is_refused() -> None:
    store = FakeStore()
    store.add_parts(COMMAND.BASE_RUNG)
    store.completions[COMMAND.BASE_RUNG] = PartitionCompletion(
        part_count=1,
        row_count=1,
        completed_at=NOW,
        run_id="stale",
        parts=(
            CompletedPart(
                relative_path=store.parts[COMMAND.BASE_RUNG][0],
                row_count=1,
                byte_count=20,
                sha256="b" * 64,
            ),
        ),
    )

    plan = COMMAND.inspect_day(store, layer=LAYER, day=DAY)

    assert plan.refusal is not None
    assert "part identities do not match" in plan.refusal


def test_locked_preflight_refuses_base_receipt_drift_before_any_put() -> None:
    store = FakeStore()
    store.add_parts(COMMAND.BASE_RUNG)

    @asynccontextmanager
    async def publication(_session: object, _lane_root: str) -> AsyncIterator[bool]:
        yield True

    @asynccontextmanager
    async def changing_day(_session: object, _key: str) -> AsyncIterator[bool]:
        store.read_digest = "c" * 64
        yield True

    with pytest.raises(COMMAND.LadderCompletionError, match="changed between dry preflight and locked preflight"):
        asyncio.run(
            COMMAND.complete_range(
                store,
                layer=LAYER,
                first=DAY,
                last=DAY,
                apply_changes=True,
                session=object(),
                publication_barrier=publication,
                lane_day_lock=changing_day,
                derive=_fake_derive,
            )
        )

    assert store.operations == []


def test_locked_preflight_refuses_legacy_marker_digest_drift_before_any_put() -> None:
    store = FakeStore()
    _add_complete_ladder(store, legacy=True)

    @asynccontextmanager
    async def publication(_session: object, _lane_root: str) -> AsyncIterator[bool]:
        yield True

    @asynccontextmanager
    async def changing_day(_session: object, _key: str) -> AsyncIterator[bool]:
        store.marker_digest_override = "c" * 64
        yield True

    with pytest.raises(COMMAND.LadderCompletionError, match="changed between dry preflight and locked preflight"):
        asyncio.run(
            COMMAND.complete_range(
                store,
                layer=LAYER,
                first=DAY,
                last=DAY,
                apply_changes=True,
                session=object(),
                publication_barrier=publication,
                lane_day_lock=changing_day,
            )
        )

    assert store.operations == []


def test_post_write_verification_reports_a_marker_that_does_not_bind_the_parts() -> None:
    store = FakeStore()
    _add_complete_ladder(store, legacy=True)
    store.corrupt_completion_writes = True

    @asynccontextmanager
    async def publication(_session: object, _lane_root: str) -> AsyncIterator[bool]:
        yield True

    @asynccontextmanager
    async def day_lock(_session: object, _key: str) -> AsyncIterator[bool]:
        yield True

    receipt = asyncio.run(
        COMMAND.complete_range(
            store,
            layer=LAYER,
            first=DAY,
            last=DAY,
            apply_changes=True,
            run_id="legacy-upgrade",
            session=object(),
            publication_barrier=publication,
            lane_day_lock=day_lock,
            now=lambda: NOW,
        )
    )

    assert receipt["applied_days"] == []
    assert receipt["failures"][0]["day"] == DAY.isoformat()
    assert "part identities do not match" in receipt["failures"][0]["error"]


def test_release_series_and_unbounded_ranges_are_rejected() -> None:
    with pytest.raises(COMMAND.LadderCompletionError, match="release_series"):
        asyncio.run(COMMAND.complete_range(FakeStore(), layer="drought", first=DAY, last=DAY))
    with pytest.raises(COMMAND.LadderCompletionError, match="at most"):
        asyncio.run(
            COMMAND.complete_range(
                FakeStore(),
                layer=LAYER,
                first=DAY,
                last=date(DAY.year + 2, DAY.month, DAY.day),
            )
        )


def test_cli_requires_apply_for_mutation() -> None:
    parsed = COMMAND._parse_arguments(
        ["--lane", LAYER, "--from-day", DAY.isoformat(), "--through-day", DAY.isoformat()]
    )
    assert parsed.apply is False
