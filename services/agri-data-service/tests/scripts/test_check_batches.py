"""Changed-test boundaries and receipt isolation, without executing validation tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from pathlib import Path

load_scripts_module("quality_receipt.py", "quality_receipt")
CHECK = load_scripts_module("check.py", "plantgeo_check_module")


def test_test_only_plan_is_sorted_and_deleted_tests_fall_back(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    for name in ("test_a.py", "test_b.py"):
        (tmp_path / "tests" / name).write_text("")
    plan = CHECK.plan_tests(("tests/test_b.py", "tests/test_a.py", "tests/test_a.py"), (), tmp_path)
    assert plan.pytest_paths == ("tests/test_a.py", "tests/test_b.py")
    assert not plan.receipt_eligible
    deleted = CHECK.plan_tests(("tests/test_old.py", "tests/test_b.py"), (), tmp_path)
    assert deleted.mode == "full"
    assert "deleted test" in deleted.reasons[0]


@pytest.mark.parametrize(
    "path",
    [
        "src/agri_data_service/new_module.py",
        "src/agri_data_service/pipeline/parquet/source_checkpoint.py",
        "tests/conftest.py",
        "tests/direct/soil/conftest.py",
        "scripts/check.py",
        "pyproject.toml",
        "src/agri_data_service/pipeline/direct/__init__.py",
    ],
)
def test_unknown_shared_and_harness_changes_require_full(path: str, tmp_path: Path) -> None:
    plan = CHECK.plan_tests((path,), (), tmp_path)
    assert plan.mode == "full"
    assert not plan.receipt_eligible


def test_direct_source_includes_cross_surface_contracts(tmp_path: Path) -> None:
    for name in ("direct", "parquet", "interface", "contract", "scripts"):
        (tmp_path / "tests" / name).mkdir(parents=True)
    (tmp_path / "tests" / "test_layer_import_contract.py").write_text("")
    plan = CHECK.plan_tests(("src/agri_data_service/pipeline/direct/soil/source.py",), (), tmp_path)
    assert plan.mode == "scoped"
    assert plan.pytest_paths == (
        "tests/contract",
        "tests/direct",
        "tests/interface",
        "tests/parquet",
        "tests/scripts",
        "tests/test_layer_import_contract.py",
    )


def test_changed_git_includes_rename_sides_and_untracked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def git(arguments: tuple[str, ...], _root: Path) -> bytes:
        calls.append(arguments)
        if arguments[0] == "diff":
            return b"tests/test_old.py\0tests/test_new.py\0"
        return b"tests/test_untracked.py\0"

    monkeypatch.setattr(CHECK, "_git_output", git)
    assert CHECK.changed_paths(tmp_path, None) == ("tests/test_new.py", "tests/test_old.py", "tests/test_untracked.py")
    assert "--no-renames" in calls[0]
    assert "HEAD" in calls[0]


def test_bad_base_falls_back_to_full_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def git(_arguments: tuple[str, ...], _root: Path) -> bytes:
        raise CHECK.GitQueryError("missing base")

    monkeypatch.setattr(CHECK, "_git_output", git)
    assert CHECK.main(["--changed", "--base", "missing", "--plan"], service_root=tmp_path) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "full"
    assert plan["receipt_eligible"] is False
    assert plan["checks"][-1]["command"] == ["pytest", "-q"]


@pytest.mark.parametrize("selection", [["--changed"], ["--batch", "direct"], ["--changed", "--base", "missing"]])
def test_scoped_receipt_refused_before_any_work(selection: list[str], tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        CHECK.main([*selection, "--write-receipt"], service_root=tmp_path)
    assert not (tmp_path / "QUALITY_RECEIPT.json").exists()


@pytest.mark.parametrize(
    "arguments", [["--base", "main"], ["--list", "--plan"], ["--list-batches", "--changed"], ["--batch", "unknown"]]
)
def test_incompatible_options_are_refused(arguments: list[str], tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        CHECK.main(arguments, service_root=tmp_path)


def test_empty_selection_does_not_accidentally_run_all_pytest(tmp_path: Path) -> None:
    plan = CHECK.plan_tests(("AGENTS.md",), (), tmp_path)
    assert plan.mode == "none"
    checks = CHECK._planned_checks(CHECK.CHECKS, plan)
    assert [check.name for check in checks] == ["format", "lint", "mypy"]


def test_batch_and_changed_test_do_not_collect_the_same_file_twice(tmp_path: Path) -> None:
    (tmp_path / "tests" / "foundation").mkdir(parents=True)
    (tmp_path / "tests" / "foundation" / "test_one.py").write_text("")
    plan = CHECK.plan_tests(("tests/foundation/test_one.py",), ("foundation",), tmp_path)
    assert plan.pytest_paths == ("tests/foundation",)
