"""Run the full quality sweep against an immutable Git snapshot and copy its receipt back.

Design and operator contract: see ``scripts/AGENTS.md`` under "Snapshot-isolated receipt updates".
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from quality_receipt import RECEIPT_FILE_NAME, read_receipt, sanitized_gate_environment, write_receipt

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence


SERVICE_RELATIVE_PATH: Final = Path("services/agri-data-service")
FULL_RECEIPT_ARGUMENTS: Final[tuple[str, ...]] = ("scripts/check.py", "--write-receipt")
VERIFY_ARGUMENTS: Final[tuple[str, ...]] = ("scripts/verify_quality_receipt.py",)
SYNC_ARGUMENTS: Final[tuple[str, ...]] = ("sync", "--locked", "--all-extras", "--project", ".")
UV_RUN_ARGUMENTS: Final[tuple[str, ...]] = ("run", "--locked", "--no-sync", "--project", ".", "python")


class SnapshotReceiptError(RuntimeError):
    """The requested Git snapshot could not be exported or certified."""


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    """One immutable root tree selected from the index or a named commit."""

    kind: str
    tree: str
    commit: str | None = None


@dataclass(frozen=True, slots=True)
class IsolatedGit:
    """Disposable Git environment whose index cannot alias the caller's index."""

    environment: dict[str, str]


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run one non-shell command and return its captured bytes."""
    try:
        completed = subprocess.run(
            tuple(command),
            cwd=cwd,
            env=None if env is None else dict(env),
            input=input_bytes,
            check=False,
            shell=False,
            capture_output=True,
        )
    except OSError as error:
        raise SnapshotReceiptError(f"unable to run {command[0]!r}: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        rendered = " ".join(command)
        raise SnapshotReceiptError(f"`{rendered}` failed with exit {completed.returncode}: {detail}")
    return completed


def _git_output(arguments: Sequence[str], repository_root: Path) -> str:
    """Return stripped UTF-8 output from one read-only Git query."""
    return _run(("git", *arguments), cwd=repository_root).stdout.decode("utf-8").strip()


def repository_root(service_root: Path) -> Path:
    """Resolve the repository containing the service."""
    return Path(_git_output(("rev-parse", "--show-toplevel"), service_root)).resolve()


def resolve_snapshot(repository: Path, commit: str | None) -> GitSnapshot:
    """Freeze either the current index tree or one named commit and its root tree."""
    if commit is None:
        return GitSnapshot(kind="index", tree=_git_output(("write-tree",), repository))
    resolved = _git_output(("rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}"), repository)
    tree = _git_output(("rev-parse", "--verify", "--end-of-options", f"{resolved}^{{tree}}"), repository)
    return GitSnapshot(kind="commit", tree=tree, commit=resolved)


def _safe_extract(payload: bytes, destination: Path) -> Path:
    """Extract only the service subtree from a Git-authored tar archive."""
    service_path = SERVICE_RELATIVE_PATH.as_posix()
    prefix = service_path + "/"
    ancestors = {
        Path(*SERVICE_RELATIVE_PATH.parts[:index]).as_posix()
        for index in range(1, len(SERVICE_RELATIVE_PATH.parts) + 1)
    }
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        members = archive.getmembers()
        outside_service = any(member.name not in ancestors and not member.name.startswith(prefix) for member in members)
        if not members or outside_service:
            raise SnapshotReceiptError("git archive contained a path outside the agri-data-service subtree")
        archive.extractall(destination, members=members, filter="data")
    exported = destination / SERVICE_RELATIVE_PATH
    if not (exported / "scripts" / "check.py").is_file():
        raise SnapshotReceiptError("selected snapshot does not contain the agri-data-service receipt writer")
    return exported


def export_snapshot(snapshot: GitSnapshot, repository: Path, destination: Path) -> Path:
    """Export the selected service bytes without consulting the working directory."""
    archive = _run(
        ("git", "archive", "--format=tar", snapshot.tree, "--", SERVICE_RELATIVE_PATH.as_posix()),
        cwd=repository,
    ).stdout
    return _safe_extract(archive, destination)


def _prepare_temporary_index(
    snapshot: GitSnapshot,
    repository: Path,
    temporary_root: Path,
) -> IsolatedGit:
    """Load the frozen tree into a disposable index without writing the caller's index."""
    git_directory = Path(_git_output(("rev-parse", "--absolute-git-dir"), repository)).resolve()
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_DIR": str(git_directory),
            "GIT_WORK_TREE": str(temporary_root.resolve()),
            "GIT_INDEX_FILE": str((temporary_root / ".quality-receipt.index").resolve()),
        }
    )
    _run(("git", "read-tree", snapshot.tree), cwd=repository, env=environment)
    return IsolatedGit(environment=environment)


def _sanitized_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Remove inherited selectors that can change Python, uv, or an individual quality gate."""
    return sanitized_gate_environment(environment)


def _environment_python(environment: Path) -> Path:
    """Return the interpreter created by uv for this exported project."""
    candidate = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not candidate.is_file():
        raise SnapshotReceiptError(f"frozen sync did not create {candidate}")
    return candidate


def _site_packages(environment: Path) -> tuple[Path, ...]:
    """Return site-package directories without importing or executing any .pth file."""
    windows = environment / "Lib" / "site-packages"
    candidates = [windows] if windows.is_dir() else list((environment / "lib").glob("python*/site-packages"))
    if not candidates:
        raise SnapshotReceiptError(f"unable to locate site-packages in {environment}")
    return tuple(path.resolve() for path in candidates)


def _validate_snapshot_environment(environment: Path, exported_service: Path, child_env: Mapping[str, str]) -> None:
    """Reject editable fallbacks outside the export and prove the package import origin."""
    allowed_roots = (environment.resolve(), exported_service.resolve())
    for site_packages in _site_packages(environment):
        for pth_path in site_packages.glob("*.pth"):
            for raw_line in pth_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith(("import ", "import\t")):
                    if "editable" in pth_path.name.lower():
                        raise SnapshotReceiptError(f"editable executable .pth fallback is not allowed: {pth_path}")
                    continue
                referenced = Path(line)
                if not referenced.is_absolute():
                    referenced = site_packages / referenced
                resolved = referenced.resolve()
                if not any(resolved.is_relative_to(root) for root in allowed_roots):
                    raise SnapshotReceiptError(f".pth path escapes the frozen export: {pth_path} -> {resolved}")

    probe = _run(
        (
            str(_environment_python(environment)),
            "-I",
            "-c",
            "from pathlib import Path; import agri_data_service; print(Path(agri_data_service.__file__).resolve())",
        ),
        cwd=exported_service,
        env=child_env,
    )
    origin = Path(probe.stdout.decode("utf-8").strip().splitlines()[-1]).resolve()
    if not origin.is_relative_to((exported_service / "src").resolve()):
        raise SnapshotReceiptError(f"agri_data_service imported outside the frozen export: {origin}")


def _sync_snapshot_environment(uv_path: str, exported_service: Path, git: IsolatedGit) -> dict[str, str]:
    """Create a frozen environment from the exported project and lock, then validate its imports."""
    for required in ("pyproject.toml", "uv.lock"):
        if not (exported_service / required).is_file():
            raise SnapshotReceiptError(f"frozen environment requires exported {required}")
    child_env = _sanitized_environment(git.environment)
    _run((uv_path, *SYNC_ARGUMENTS), cwd=exported_service, env=child_env)
    environment = exported_service / ".venv"
    _validate_snapshot_environment(environment, exported_service, child_env)
    return child_env


def _run_quality_command(
    uv_path: str,
    arguments: Sequence[str],
    *,
    exported_service: Path,
    child_env: Mapping[str, str],
) -> int:
    """Run one receipt command inside the already-synchronized frozen environment."""
    completed = subprocess.run(
        (uv_path, *UV_RUN_ARGUMENTS, *arguments),
        cwd=exported_service,
        env=child_env,
        check=False,
        shell=False,
    )
    return completed.returncode


def _record_snapshot(receipt_path: Path, snapshot: GitSnapshot) -> None:
    """Add the Git identity that selected the already-digested receipt bytes."""
    receipt = read_receipt(receipt_path)
    receipt["snapshot"] = {
        "kind": snapshot.kind,
        "git_tree": snapshot.tree,
        "commit": snapshot.commit,
    }
    write_receipt(receipt, receipt_path)


def _stage_receipt_bytes(payload: bytes, destination: Path) -> Path:
    """Write one fsynced, uniquely named same-directory replacement candidate."""
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


@contextmanager
def _receipt_install_lock(destination: Path) -> Iterator[None]:
    """Exclude concurrent receipt installers without sharing their temporary filenames."""
    lock_path = destination.with_name(f".{destination.name}.install.lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise SnapshotReceiptError(f"another receipt installation owns {lock_path}") from error
    os.close(descriptor)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _install_receipt(source: Path, destination: Path, assert_current: Callable[[], None]) -> None:
    """Atomically install under a lock, rolling back if the index moves after replacement."""
    with _receipt_install_lock(destination):
        previous = destination.read_bytes() if destination.is_file() else None
        assert_current()
        candidate = _stage_receipt_bytes(source.read_bytes(), destination)
        try:
            os.replace(candidate, destination)
            assert_current()
        except BaseException:
            candidate.unlink(missing_ok=True)
            if previous is None:
                destination.unlink(missing_ok=True)
            else:
                rollback = _stage_receipt_bytes(previous, destination)
                os.replace(rollback, destination)
            raise


def _assert_snapshot_current(snapshot: GitSnapshot, repository: Path) -> None:
    """Refuse an index snapshot once the caller's staged tree has changed."""
    if snapshot.kind == "index" and resolve_snapshot(repository, None).tree != snapshot.tree:
        raise SnapshotReceiptError("the Git index changed while the isolated sweep ran; re-run")


def update_receipt(service_root: Path, *, commit: str | None = None) -> int:
    """Certify one frozen snapshot and install its verified receipt into the working tree."""
    repository = repository_root(service_root)
    snapshot = resolve_snapshot(repository, commit)
    uv_path = shutil.which("uv")
    if uv_path is None:
        raise SnapshotReceiptError("unable to find the 'uv' executable on PATH")

    with tempfile.TemporaryDirectory(prefix="plantgeo-quality-receipt-") as temporary_name:
        temporary_root = Path(temporary_name)
        exported = export_snapshot(snapshot, repository, temporary_root)
        isolated_git = _prepare_temporary_index(snapshot, repository, temporary_root)
        child_env = _sync_snapshot_environment(uv_path, exported, isolated_git)
        if (
            _run_quality_command(
                uv_path,
                FULL_RECEIPT_ARGUMENTS,
                exported_service=exported,
                child_env=child_env,
            )
            != 0
        ):
            raise SnapshotReceiptError("the isolated full quality sweep was not green")

        receipt_path = exported / RECEIPT_FILE_NAME
        _record_snapshot(receipt_path, snapshot)
        if (
            _run_quality_command(
                uv_path,
                VERIFY_ARGUMENTS,
                exported_service=exported,
                child_env=child_env,
            )
            != 0
        ):
            raise SnapshotReceiptError("the isolated Docker-compatible receipt verifier refused the result")
        _install_receipt(
            receipt_path,
            service_root / RECEIPT_FILE_NAME,
            lambda: _assert_snapshot_current(snapshot, repository),
        )

    print(
        json.dumps(
            {
                "event": "quality_receipt_updated",
                "snapshot_kind": snapshot.kind,
                "git_tree": snapshot.tree,
                "commit": snapshot.commit,
                "receipt": str(service_root / RECEIPT_FILE_NAME),
            },
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the snapshot updater CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        metavar="REF",
        help="Certify a named commit instead of the current staged/index snapshot.",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, service_root: Path | None = None) -> int:
    """Run the snapshot-isolated receipt update."""
    arguments = build_parser().parse_args(argv)
    root = Path(__file__).resolve().parent.parent if service_root is None else service_root.resolve()
    try:
        return update_receipt(root, commit=arguments.commit)
    except SnapshotReceiptError as error:
        print(f"QUALITY RECEIPT UPDATE REFUSED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
