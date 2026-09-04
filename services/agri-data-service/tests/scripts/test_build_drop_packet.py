"""The drop-packet operator surface: what it refuses, and what its exit code means.

These run against the real checkout, which is safe because the whole package is read-only by
construction (`tests/retirement/test_readers.py` asserts it has no way to open a connection).
"""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING

import pytest

from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from pathlib import Path

BUILD_DROP_PACKET = load_scripts_module("build_drop_packet.py", "build_drop_packet")


def test_apply_is_refused_before_anything_is_parsed(capsys: pytest.CaptureFixture[str]) -> None:
    """There is no --apply and the refusal teaches why, rather than reading as an argparse typo."""
    code = BUILD_DROP_PACKET.main(["--apply", "--relation", "geo.drought_areas"])

    assert code == BUILD_DROP_PACKET.EXIT_REFUSED
    assert "there is no --apply" in capsys.readouterr().err


def test_listing_the_ledger_exits_zero_and_groups_by_class(capsys: pytest.CaptureFixture[str]) -> None:
    """The index a packet build starts from, read straight out of the A3 evidence file."""
    code = BUILD_DROP_PACKET.main(["--list"])

    payload = json.loads(capsys.readouterr().out)

    assert code == BUILD_DROP_PACKET.EXIT_READY
    assert "geo.features" in payload["drop_after_parquet_proof"]
    assert "agri.spatial_cell" in payload["drop_now"]


def test_a_blocked_packet_exits_one_because_the_exit_code_is_the_gate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A CI step or an `&&` chain reads the code, not the prose -- the parity modules' own rule."""
    code = BUILD_DROP_PACKET.main(["--relation", "agri.spatial_cell", "--as-of", "2026-09-04", "--json"])

    payload = json.loads(capsys.readouterr().out)

    assert code == BUILD_DROP_PACKET.EXIT_BLOCKED
    assert payload["verdict"] == "blocked"
    assert "survival_dependency" in {blocker["code"] for blocker in payload["blockers"]}


def test_an_unledgered_relation_is_refused_rather_than_synthesised(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A relation A3 never censused has not been through the reader survey a packet claims it has."""
    code = BUILD_DROP_PACKET.main(["--relation", "geo.not_in_the_inventory"])

    assert code == BUILD_DROP_PACKET.EXIT_REFUSED
    assert "retirement-inventory.md" in capsys.readouterr().err


def test_a_table_drop_of_geo_features_emits_no_packet(capsys: pytest.CaptureFixture[str]) -> None:
    """Fact 1 reaches the operator surface as a refusal with no artifact, not as a blocked packet."""
    code = BUILD_DROP_PACKET.main(["--relation", "geo.features", "--form", "table_drop"])

    captured = capsys.readouterr()

    assert code == BUILD_DROP_PACKET.EXIT_REFUSED
    assert "no packet was emitted" in captured.err
    assert captured.out == ""


def test_a_key_value_flag_refuses_a_half_written_pair() -> None:
    """`--layer-parity-receipt vegetation` would otherwise drop the path silently."""
    with pytest.raises(argparse.ArgumentTypeError, match="key=value"):
        BUILD_DROP_PACKET._receipt_pair("vegetation")


def test_a_naive_epoch_timestamp_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    """An epoch comparison against a local wall clock is one nobody else can reproduce."""
    code = BUILD_DROP_PACKET.main(
        [
            "--relation",
            "geo.features",
            "--layer",
            "fire-perimeters",
            "--twin-newest-completion-at",
            "fire-perimeters=2026-09-05T00:00:00",
        ]
    )

    assert code == BUILD_DROP_PACKET.EXIT_REFUSED
    assert "no timezone" in capsys.readouterr().err


def test_output_writes_the_packet_and_reports_the_verdict_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The packet is the artifact; the verdict on stderr keeps stdout a clean file when redirected."""
    destination = tmp_path / "packets" / "geo.mv_soil_survey_grid.md"

    code = BUILD_DROP_PACKET.main(
        ["--relation", "geo.mv_soil_survey_grid", "--as-of", "2026-09-04", "--output", str(destination)]
    )

    captured = capsys.readouterr()

    assert code == BUILD_DROP_PACKET.EXIT_BLOCKED
    assert destination.read_text(encoding="utf-8").startswith("---\ntype: evidence")
    assert "blocked: wrote" in captured.err


def test_the_help_text_states_that_nothing_is_fired() -> None:
    """The refusal has to be discoverable from `--help`, not only from reading the source."""
    help_text = BUILD_DROP_PACKET.parser().format_help()

    assert "no `pg_dump`" in help_text
    assert "no migration" in help_text
