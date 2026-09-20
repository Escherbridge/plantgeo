"""Snapshot-isolation tests for ``scripts/update_quality_receipt.py``."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

QUALITY_RECEIPT = load_scripts_module("quality_receipt.py", "quality_receipt")
UPDATE = load_scripts_module("update_quality_receipt.py", "plantgeo_update_quality_receipt_module")

pytestmark = pytest.mark.skipif(UPDATE.shutil.which("git") is None, reason="these tests need git")

SERVICE = UPDATE.SERVICE_RELATIVE_PATH


def _git(arguments: Sequence[str], root: Path) -> str:
    completed = subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _repository(root: Path) -> Path:
    _git(("init", "-q"), root)
    _git(("config", "user.email", "receipt-tests@example.invalid"), root)
    _git(("config", "user.name", "Receipt Tests"), root)
    service = root / SERVICE
    (service / "src").mkdir(parents=True)
    (service / "scripts").mkdir()
    (service / "src" / "module.py").write_text("value = 1\n", encoding="utf-8")
    (service / "scripts" / "check.py").write_text("# fixture\n", encoding="utf-8")
    _git(("add", "-A"), root)
    _git(("commit", "-qm", "baseline"), root)
    return service


def test_index_export_reads_staged_bytes_and_ignores_unstaged_and_untracked_files(tmp_path: Path) -> None:
    """The index snapshot is exactly what a future commit would contain, not the mutable disk."""
    service = _repository(tmp_path)
    module = service / "src" / "module.py"
    module.write_text("value = 2\n", encoding="utf-8")
    _git(("add", module.relative_to(tmp_path).as_posix()), tmp_path)
    module.write_text("value = 3\n", encoding="utf-8")
    (service / "src" / "untracked.py").write_text("untracked = True\n", encoding="utf-8")

    snapshot = UPDATE.resolve_snapshot(tmp_path, None)
    exported = UPDATE.export_snapshot(snapshot, tmp_path, tmp_path / "export")

    assert snapshot.kind == "index"
    assert (exported / "src" / "module.py").read_text(encoding="utf-8") == "value = 2\n"
    assert not (exported / "src" / "untracked.py").exists()


def test_named_commit_export_ignores_a_newer_index(tmp_path: Path) -> None:
    """A named commit remains stable even when the caller has staged later work."""
    service = _repository(tmp_path)
    committed = _git(("rev-parse", "HEAD"), tmp_path)
    module = service / "src" / "module.py"
    module.write_text("value = 2\n", encoding="utf-8")
    _git(("add", module.relative_to(tmp_path).as_posix()), tmp_path)

    snapshot = UPDATE.resolve_snapshot(tmp_path, committed)
    exported = UPDATE.export_snapshot(snapshot, tmp_path, tmp_path / "export")

    assert snapshot.kind == "commit"
    assert snapshot.commit == committed
    assert (exported / "src" / "module.py").read_text(encoding="utf-8") == "value = 1\n"


def test_full_receipt_command_has_no_scoped_selection_flags() -> None:
    """The updater can never turn a changed/batch test run into a release receipt."""
    assert UPDATE.FULL_RECEIPT_ARGUMENTS == ("scripts/check.py", "--write-receipt")
    assert not {"--changed", "--batch", "--only"} & set(UPDATE.FULL_RECEIPT_ARGUMENTS)


def test_snapshot_metadata_does_not_change_the_tree_digest(tmp_path: Path) -> None:
    """Receipt metadata identifies the source tree while remaining outside its own digest."""
    service = _repository(tmp_path)
    digest_before = QUALITY_RECEIPT.compute_tree_digest(service)
    receipt = {
        "schema_version": QUALITY_RECEIPT.RECEIPT_SCHEMA_VERSION,
        "digest_domain": QUALITY_RECEIPT.DIGEST_DOMAIN.decode(),
        "checks": [{"name": "pytest", "status": "pass"}],
    }
    path = service / QUALITY_RECEIPT.RECEIPT_FILE_NAME
    QUALITY_RECEIPT.write_receipt(receipt, path)

    UPDATE._record_snapshot(path, UPDATE.GitSnapshot(kind="index", tree="a" * 40))

    assert QUALITY_RECEIPT.compute_tree_digest(service) == digest_before
    assert QUALITY_RECEIPT.read_receipt(path)["snapshot"] == {
        "kind": "index",
        "git_tree": "a" * 40,
        "commit": None,
    }


def test_disposable_index_reads_snapshot_without_changing_caller_index(tmp_path: Path) -> None:
    """The writer sees the snapshot while the caller's real index remains byte-for-byte unchanged."""
    service = _repository(tmp_path)
    module = service / "src" / "module.py"
    module.write_text("value = 2\n", encoding="utf-8")
    _git(("add", module.relative_to(tmp_path).as_posix()), tmp_path)
    caller_index_before = _git(("ls-files", "--stage"), tmp_path)
    snapshot = UPDATE.resolve_snapshot(tmp_path, None)
    UPDATE.export_snapshot(snapshot, tmp_path, tmp_path / "export")

    isolated = UPDATE._prepare_temporary_index(snapshot, tmp_path, tmp_path / "export")
    isolated_listing = subprocess.run(
        ["git", "ls-files", "--stage", "--", SERVICE.as_posix()],
        cwd=tmp_path / "export",
        env=isolated.environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert f"{SERVICE.as_posix()}/src/module.py" in isolated_listing
    assert _git(("ls-files", "--stage"), tmp_path) == caller_index_before


def test_staged_deletion_cannot_import_unstaged_working_tree_fallback(tmp_path: Path) -> None:
    """A deleted staged module stays absent even when inherited PYTHONPATH names the checkout."""
    service = _repository(tmp_path)
    probe = service / "src" / "snapshot_probe.py"
    probe.write_text("VALUE = 'committed'\n", encoding="utf-8")
    _git(("add", probe.relative_to(tmp_path).as_posix()), tmp_path)
    _git(("commit", "-qm", "add probe"), tmp_path)
    probe.unlink()
    _git(("add", probe.relative_to(tmp_path).as_posix()), tmp_path)
    probe.write_text("VALUE = 'unstaged fallback'\n", encoding="utf-8")
    snapshot = UPDATE.resolve_snapshot(tmp_path, None)
    export_root = tmp_path / "export"
    exported = UPDATE.export_snapshot(snapshot, tmp_path, export_root)
    child_environment = UPDATE._sanitized_environment(
        {**UPDATE.os.environ, "PYTHONPATH": str((service / "src").resolve())}
    )

    completed = subprocess.run(
        [sys.executable, "-c", "import snapshot_probe"],
        cwd=exported,
        env=child_environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "PYTHONPATH" not in child_environment


def test_selector_environment_is_sanitized_without_dropping_runtime_configuration() -> None:
    """Gate selectors are removed while ordinary application/test configuration survives."""
    source = {
        "PATH": "tools",
        "PYTHONPATH": "checkout/src",
        "PYTEST_ADDOPTS": "-k one_test",
        "MYPY_CONFIG_FILE": "other.ini",
        "RUFF_CONFIG": "other.toml",
        "UV_PROJECT": "other-project",
        "UV_PROJECT_ENVIRONMENT": "checkout/.venv",
        "VIRTUAL_ENV": "checkout/.venv",
        "AGRI_TEST_DATABASE_URL": "postgresql://test.invalid/db",
    }

    sanitized = UPDATE._sanitized_environment(source)

    assert sanitized == {
        "PATH": "tools",
        "AGRI_TEST_DATABASE_URL": "postgresql://test.invalid/db",
    }


def test_sync_is_locked_and_bound_to_exported_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The environment is synchronized from the exported lock, never a checkout virtualenv."""
    exported = tmp_path / "service"
    exported.mkdir()
    (exported / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n", encoding="utf-8")
    (exported / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def fake_run(command: Sequence[str], *, cwd: Path, env: dict[str, str], **_kwargs: object) -> object:
        calls.append((tuple(command), cwd, env))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(UPDATE, "_run", fake_run)
    monkeypatch.setattr(UPDATE, "_validate_snapshot_environment", lambda *_args: None)
    isolated = UPDATE.IsolatedGit(
        environment={
            "PATH": "tools",
            "PYTHONPATH": "leak",
            "GIT_DIR": "real-git-dir",
            "GIT_WORK_TREE": "temporary-worktree",
            "GIT_INDEX_FILE": "disposable-index",
        }
    )

    child_env = UPDATE._sync_snapshot_environment("uv", exported, isolated)

    assert calls == [(("uv", *UPDATE.SYNC_ARGUMENTS), exported, {"PATH": "tools"})]
    assert child_env == {"PATH": "tools"}


def test_quality_command_exposes_disposable_git_without_leaking_it_to_environment_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the receipt writer gets snapshot Git ownership; uv and later child gates do not."""
    environment = tmp_path / ".venv"
    interpreter = environment / "Scripts" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"")
    observed: dict[str, object] = {}

    def fake_run(command: Sequence[str], **kwargs: object) -> object:
        observed.update(command=tuple(command), **kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(UPDATE.subprocess, "run", fake_run)
    isolated = UPDATE.IsolatedGit(
        environment={
            "GIT_DIR": "git-dir",
            "GIT_WORK_TREE": "work-tree",
            "GIT_INDEX_FILE": "index-file",
        }
    )

    result = UPDATE._run_quality_command(
        UPDATE.FULL_RECEIPT_ARGUMENTS,
        exported_service=tmp_path,
        child_env={"PATH": "tools"},
        git=isolated,
    )

    assert result == 0
    assert observed["command"] == (str(interpreter), *UPDATE.FULL_RECEIPT_ARGUMENTS)
    assert observed["env"] == {
        "PATH": "tools",
        "GIT_DIR": "git-dir",
        "GIT_WORK_TREE": "work-tree",
        "GIT_INDEX_FILE": "index-file",
    }


def test_sync_refuses_an_export_without_its_lock(tmp_path: Path) -> None:
    """A project snapshot cannot silently resolve dependencies without its selected uv.lock."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n", encoding="utf-8")

    with pytest.raises(UPDATE.SnapshotReceiptError, match=r"exported uv\.lock"):
        UPDATE._sync_snapshot_environment("uv", tmp_path, UPDATE.IsolatedGit(environment={}))


def test_editable_pth_cannot_fall_back_to_checkout(tmp_path: Path) -> None:
    """An editable path outside the export is refused before any quality command runs."""
    environment = tmp_path / "environment"
    site_packages = environment / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    external = tmp_path / "checkout" / "src"
    external.mkdir(parents=True)
    (site_packages / "_editable_impl_fixture.pth").write_text(str(external), encoding="utf-8")
    exported = tmp_path / "export"
    exported.mkdir()

    with pytest.raises(UPDATE.SnapshotReceiptError, match="pth path escapes"):
        UPDATE._validate_snapshot_environment(environment, exported, {})


def test_final_index_race_rolls_back_installed_receipt(tmp_path: Path) -> None:
    """An index move after replace refuses success and restores the prior receipt."""
    source = tmp_path / "new.json"
    destination = tmp_path / "QUALITY_RECEIPT.json"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    checks = 0
    expected_index_checks = 2

    def assert_current() -> None:
        nonlocal checks
        checks += 1
        if checks == expected_index_checks:
            raise UPDATE.SnapshotReceiptError("index moved")

    with pytest.raises(UPDATE.SnapshotReceiptError, match="index moved"):
        UPDATE._install_receipt(source, destination, assert_current)

    assert checks == expected_index_checks
    assert destination.read_bytes() == b"old"
    assert not list(tmp_path.glob(".QUALITY_RECEIPT.json.*"))


def test_receipt_candidates_use_unique_same_directory_names(tmp_path: Path) -> None:
    """Concurrent attempts cannot overwrite one another's pre-replace temporary file."""
    destination = tmp_path / "QUALITY_RECEIPT.json"
    first = UPDATE._stage_receipt_bytes(b"first", destination)
    second = UPDATE._stage_receipt_bytes(b"second", destination)
    try:
        assert first.parent == destination.parent == second.parent
        assert first != second
        assert first.read_bytes() == b"first"
        assert second.read_bytes() == b"second"
    finally:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)
