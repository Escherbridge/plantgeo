"""The assembled packet and its verdict: every blocker, and the one path that reaches `ready`."""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.retirement.ledger import DropForm, DropFormRefusedError, load_ledger
from agri_data_service.retirement.packet import BlockerCode, DropPacket, DropPacketRequest, build_packet
from agri_data_service.retirement.parity import RecordedLaneWriteProbe
from tests.retirement import build_checkout, clean_drought_receipt, instant, short_drought_receipt

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_AS_OF: Final = date(2026, 9, 4)
_DIGEST: Final = "b" * 64


def _build(  # noqa: PLR0913 - each argument is one independent coordinate of the synthetic packet
    root: Path,
    relation: str,
    *,
    form: DropForm | None = None,
    layers: tuple[str, ...] = (),
    parity_receipts: Mapping[str, Mapping[str, object]] | None = None,
    parity_receipt_sources: Mapping[str, str] | None = None,
    probe: RecordedLaneWriteProbe | None = None,
    archive_sha256: str | None = None,
    co_migration_paths: tuple[str, ...] = (),
    layer_reader_proofs: Mapping[str, str] | None = None,
) -> DropPacket:
    """Build one packet against a synthetic checkout, with sensible defaults for the unrelated parts."""
    request = DropPacketRequest(
        relation=relation,
        repository_root=root,
        as_of=_AS_OF,
        form=form,
        layers=layers,
        parity_receipts=parity_receipts or {},
        parity_receipt_sources=parity_receipt_sources or {},
        probe=probe or RecordedLaneWriteProbe(recorded={}),
        archive_sha256=archive_sha256,
        co_migration_paths=co_migration_paths,
        layer_reader_proofs=layer_reader_proofs or {},
    )
    return build_packet(request, load_ledger(root))


def _codes(packet: DropPacket) -> set[BlockerCode]:
    """Return the distinct blocker codes a packet carries."""
    return {blocker.code for blocker in packet.blockers}


def _proven_drought(root: Path, receipt: Mapping[str, object] | None = None) -> DropPacket:
    """Build the one relation that can reach `ready`: dedicated table, bound parity, no dependents."""
    return _build(
        root,
        "geo.drought_areas",
        parity_receipts={"geo.drought_areas": receipt or clean_drought_receipt()},
        parity_receipt_sources={"geo.drought_areas": "evidence/drought-parity.json"},
        archive_sha256=_DIGEST,
    )


def test_a_fully_proven_relation_reaches_ready(tmp_path: Path) -> None:
    """All three D1 parts present, no readers: this is the only shape that clears."""
    packet = _proven_drought(build_checkout(tmp_path))

    assert packet.blockers == ()
    assert packet.verdict == "ready"


def test_an_unrun_pg_dump_blocks_on_its_own(tmp_path: Path) -> None:
    """A missing archived snapshot is a hard stop in the track's tripwires, not a footnote."""
    packet = _build(
        build_checkout(tmp_path),
        "geo.drought_areas",
        parity_receipts={"geo.drought_areas": clean_drought_receipt()},
    )

    assert _codes(packet) == {BlockerCode.ARCHIVE_SNAPSHOT_OWED}
    assert packet.verdict == "blocked"


def test_under_coverage_blocks_and_is_named_as_under_coverage(tmp_path: Path) -> None:
    """D1: under-coverage is a blocker, never waived by a note."""
    packet = _proven_drought(build_checkout(tmp_path), short_drought_receipt())

    assert BlockerCode.PARITY_UNDER_COVERAGE in _codes(packet)


def test_a_live_reader_blocks_a_whole_object_drop(tmp_path: Path) -> None:
    """Fact 2, general case: the scan is the measurement and it outranks every classification."""
    root = build_checkout(tmp_path, files={"src/lib/server/x.ts": "sql`SELECT 1 FROM geo.drought_areas`\n"})

    packet = _proven_drought(root)

    assert BlockerCode.LIVE_READERS in _codes(packet)


def test_a_drop_now_row_meeting_a_live_reader_is_loudly_contradicted(tmp_path: Path) -> None:
    """Fact 2, the `agri.spatial_cell` case: the inventory row itself is the thing that is wrong."""
    root = build_checkout(tmp_path, files={"src/lib/server/x.ts": "FROM geo.mv_orphan\n"})

    packet = _build(root, "geo.mv_orphan")

    assert BlockerCode.INVENTORY_CONTRADICTED in _codes(packet)
    assert BlockerCode.LIVE_READERS in _codes(packet)


def test_a_recorded_survival_dependency_refuses_even_with_a_clean_tree(tmp_path: Path) -> None:
    """The registry keeps the refusal alive if a future edit ever narrows a scan surface."""
    packet = _build(build_checkout(tmp_path), "agri.spatial_cell", archive_sha256=_DIGEST)

    assert BlockerCode.SURVIVAL_DEPENDENCY in _codes(packet)
    detail = next(blocker.detail for blocker in packet.blockers if blocker.code is BlockerCode.SURVIVAL_DEPENDENCY)
    assert "vegetation_ndvi_plane.py" in detail


def test_geo_features_emits_no_packet_at_all_for_a_table_drop(tmp_path: Path) -> None:
    """Fact 1: a blocked packet would imply the condition could clear. It cannot."""
    with pytest.raises(DropFormRefusedError, match="permitted form"):
        _build(build_checkout(tmp_path), "geo.features", form=DropForm.TABLE_DROP)


def test_a_row_delete_carries_one_parity_section_per_layer(tmp_path: Path) -> None:
    """Each of the seven layers earns its own receipt even though they share one relation."""
    packet = _build(build_checkout(tmp_path), "geo.features")

    assert len(packet.parity_sections) == 7
    assert {section.scope for section in packet.parity_sections} == set(packet.layers)


def test_fire_perimeters_is_never_reported_as_under_covered_before_its_twin_is_rewritten(
    tmp_path: Path,
) -> None:
    """Fact 3: the epoch outranks the receipt, so the two states stay distinguishable."""
    packet = _build(
        build_checkout(tmp_path),
        "geo.features",
        layers=("fire-perimeters",),
        probe=RecordedLaneWriteProbe(recorded={"fire-perimeters": instant(1)}),
    )

    assert BlockerCode.PARQUET_TWIN_NOT_REWRITTEN in _codes(packet)
    assert BlockerCode.PARITY_UNDER_COVERAGE not in _codes(packet)


def test_an_unproven_twin_rewrite_is_its_own_blocker(tmp_path: Path) -> None:
    """With nothing recorded about the twin, the packet says unproven rather than picking a side."""
    packet = _build(build_checkout(tmp_path), "geo.features", layers=("fire-perimeters",))

    assert BlockerCode.PARQUET_TWIN_REWRITE_UNPROVEN in _codes(packet)


def test_a_row_delete_deletes_and_archives_only_the_layers_it_covers(tmp_path: Path) -> None:
    """A one-layer proof must never authorise a seven-layer DELETE."""
    packet = _build(build_checkout(tmp_path), "geo.features", layers=("sensors",))

    assert "'sensors'" in packet.drop_statement
    assert "'vegetation'" not in packet.drop_statement
    assert all("'vegetation'" not in command.command for command in packet.archive.commands)


def test_a_row_delete_owes_a_named_reader_proof_per_layer(tmp_path: Path) -> None:
    """A grep over a surviving relation is evidence, not proof; the human citation is required."""
    root = build_checkout(tmp_path)

    owed = _build(root, "geo.features", layers=("sensors",))
    proven = _build(
        root,
        "geo.features",
        layers=("sensors",),
        layer_reader_proofs={"sensors": "conductor/.../c1-tile-function-move.md"},
    )

    assert BlockerCode.LAYER_READER_PROOF_OWED in _codes(owed)
    assert BlockerCode.LAYER_READER_PROOF_OWED not in _codes(proven)


def test_dropping_the_signal_matview_owes_a_same_migration_redefinition(tmp_path: Path) -> None:
    """Fact 4: `geo.v_observation_day_census` is redefined in the same migration or nothing moves."""
    packet = _build(build_checkout(tmp_path), "geo.mv_signal_observation_day", archive_sha256=_DIGEST)

    assert BlockerCode.DEPENDENT_REDEFINITION_OWED in _codes(packet)
    assert any("v_observation_day_census" in precondition for precondition in packet.migration_preconditions)


def test_a_co_migration_that_names_the_dependent_object_clears_the_debt(tmp_path: Path) -> None:
    """The clearing condition is a real file in this checkout, not an assertion in the packet."""
    root = build_checkout(
        tmp_path,
        files={"drizzle/0040_x.sql": "CREATE OR REPLACE VIEW geo.v_observation_day_census AS SELECT 1;\n"},
    )

    packet = _build(
        root,
        "geo.mv_signal_observation_day",
        archive_sha256=_DIGEST,
        co_migration_paths=("drizzle/0040_x.sql",),
    )

    assert BlockerCode.DEPENDENT_REDEFINITION_OWED not in _codes(packet)
    assert BlockerCode.CASCADE_FORBIDDEN not in _codes(packet)


def test_a_co_migration_using_cascade_is_refused(tmp_path: Path) -> None:
    """A CASCADE removes the dependent view instead of redefining it -- the outage this prevents."""
    root = build_checkout(
        tmp_path,
        files={"drizzle/0040_x.sql": "DROP VIEW geo.v_observation_day_census CASCADE;\n"},
    )

    packet = _build(
        root,
        "geo.mv_signal_observation_day",
        archive_sha256=_DIGEST,
        co_migration_paths=("drizzle/0040_x.sql",),
    )

    assert BlockerCode.CASCADE_FORBIDDEN in _codes(packet)


def test_a_named_co_migration_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    """A path nobody checked is exactly how a precondition gets claimed without being met."""
    packet = _build(
        build_checkout(tmp_path),
        "geo.mv_signal_observation_day",
        archive_sha256=_DIGEST,
        co_migration_paths=("drizzle/9999_absent.sql",),
    )

    assert BlockerCode.CO_MIGRATION_MISSING in _codes(packet)


def test_no_generated_statement_ever_contains_cascade(tmp_path: Path) -> None:
    """The guard is in `_drop_statement`, so no future form can reintroduce one quietly."""
    root = build_checkout(tmp_path)

    statements = [
        _build(root, relation).drop_statement
        for relation in ("geo.drought_areas", "geo.mv_signal_observation_day", "geo.v_thing", "geo.features")
    ]

    assert all("CASCADE" not in statement.upper() for statement in statements)


def test_a_keep_row_is_refused(tmp_path: Path) -> None:
    """The executor's `agri.job_*` ledger is an explicit track non-goal."""
    packet = _build(build_checkout(tmp_path), "agri.job_run", archive_sha256=_DIGEST)

    assert BlockerCode.INVENTORY_CLASS_KEEP in _codes(packet)


def test_a_classification_the_parser_cannot_map_is_refused(tmp_path: Path) -> None:
    """An unreadable ledger row must never authorise a drop by default."""
    packet = _build(build_checkout(tmp_path), "geo.mystery", archive_sha256=_DIGEST)

    assert BlockerCode.INVENTORY_CLASS_UNRESOLVED in _codes(packet)


def test_the_markdown_leads_with_the_verdict_and_carries_the_machine_body(tmp_path: Path) -> None:
    """A reader must not have to scroll to learn whether the drop is allowed."""
    rendered = _proven_drought(build_checkout(tmp_path)).to_markdown()

    assert "**Verdict: READY**" in rendered.split("## Ledger row")[0]
    assert "```json" in rendered
    assert "type: evidence" in rendered.splitlines()[1]


def test_the_json_body_names_every_blocker_with_a_code_and_a_detail(tmp_path: Path) -> None:
    """A packet is machine-readable so a later index can group drops by why they are stuck."""
    packet = _build(build_checkout(tmp_path), "geo.drought_areas")
    body = packet.to_json_dict()

    assert body["verdict"] == "blocked"
    assert [blocker.to_json_dict() for blocker in packet.blockers] == body["blockers"]
    assert all(blocker.code and blocker.detail for blocker in packet.blockers)
