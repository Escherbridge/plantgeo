"""Run the service's ad-hoc validation checks."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from quality_receipt import (
    DIGEST_DIRECTORIES,
    DIGEST_DOMAIN,
    DIGEST_FILES,
    RECEIPT_FILE_NAME,
    RECEIPT_SCHEMA_VERSION,
    SERVICE_ROOT,
    compute_digest,
    compute_tree_digest,
    digest_input_paths,
    is_digest_input,
    normalize_content,
    write_receipt,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class GitQueryError(RuntimeError):
    """Git could not answer what this tree's digest inputs look like as committed bytes."""


@dataclass(frozen=True)
class CheckDefinition:
    """Define one validation command."""

    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class CheckResult:
    """Record one validation command's outcome."""

    name: str
    returncode: int
    duration_seconds: float
    output: str


@dataclass(frozen=True)
class IndexSnapshot:
    """What git's index says about this service's digest inputs."""

    blobs: Mapping[str, bytes]
    untracked: tuple[str, ...]
    ignored: tuple[str, ...]


@dataclass(frozen=True)
class CommittedTreeVerdict:
    """Every way this working tree's digest inputs disagree with the bytes git has staged."""

    untracked: tuple[str, ...]
    modified: tuple[str, ...]
    missing_from_disk: tuple[str, ...]
    ignored: tuple[str, ...]
    disk_digest: tuple[str, int]
    index_digest: tuple[str, int]

    @property
    def is_clean(self) -> bool:
        """Return whether a fresh checkout of the index would digest exactly like this tree."""
        differences = self.untracked + self.modified + self.missing_from_disk + self.ignored
        return not differences and self.disk_digest == self.index_digest


if TYPE_CHECKING:
    #: Runs one check and reports its outcome. Injected so tests need no subprocess.
    CheckRunner = Callable[[CheckDefinition, str], CheckResult]

    #: Reads git's staged view of the digest inputs. Injected so tests need no repository.
    IndexReader = Callable[[Path], IndexSnapshot]

    #: Runs one read-only git command and returns its raw stdout. Injected so a test can fake what
    #: `git ls-files -s` printed without a repository; `_git_output`'s optional batch-stdin parameter
    #: is not part of this narrower shape because `_staged_blob_ids` never sends one.
    GitOutputReader = Callable[[Sequence[str], Path], bytes]


CHECKS: Final[tuple[CheckDefinition, ...]] = (
    CheckDefinition("format", ("ruff", "format", "--check", "src", "tests", "scripts")),
    CheckDefinition("lint", ("ruff", "check", "src", "tests", "scripts")),
    CheckDefinition("mypy", ("mypy", "src", "scripts")),
    CheckDefinition("pytest", ("pytest", "-q")),
)

#: Every child runs under `--no-sync`. A bare `uv run` re-resolves the environment from the lock's
#: default groups, which strips the dev group -- pytest and ruff vanish mid-sweep and the gate
#: reports a tooling failure as a code failure. Recorded incident; do not drop the flag.
UV_RUN_PREFIX: Final[tuple[str, ...]] = ("run", "--no-sync")

#: Read for the receipt. Each is `<tool> --version` under the same `uv run --no-sync`, so the
#: recorded version is the one that actually judged the tree.
RECORDED_TOOLS: Final[tuple[str, ...]] = ("ruff", "mypy", "pytest")

#: `<mode> <blob id> <stage>` before the tab in one `git ls-files -s` record.
INDEX_ENTRY_FIELD_COUNT: Final = 3

#: `<blob id> <type> <size>` in one `git cat-file --batch` response header.
BATCH_HEADER_FIELD_COUNT: Final = 3


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", metavar="CHECK[,CHECK...]", help="Run only the named checks.")
    parser.add_argument("--list", action="store_true", help="List available check names and exit.")
    parser.add_argument(
        "--write-receipt",
        action="store_true",
        help="After a green run of every check, write QUALITY_RECEIPT.json locking this exact tree.",
    )
    return parser


def select_checks(raw_only: str | None, parser: argparse.ArgumentParser) -> tuple[CheckDefinition, ...]:
    """Return the requested checks in their standard order."""
    if raw_only is None:
        return CHECKS

    requested = tuple(name.strip() for name in raw_only.split(","))
    if not all(requested):
        parser.error("--only requires one or more comma-separated check names")

    known_names = {check.name for check in CHECKS}
    unknown_names = sorted(set(requested) - known_names)
    if unknown_names:
        parser.error(f"unknown check name(s): {', '.join(unknown_names)}; use --list to see available checks")

    requested_names = set(requested)
    return tuple(check for check in CHECKS if check.name in requested_names)


def run_check(check: CheckDefinition, uv_path: str) -> CheckResult:
    """Run one check and capture its combined output."""
    started_at = time.perf_counter()
    try:
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            (uv_path, *UV_RUN_PREFIX, *check.command),
            cwd=Path.cwd(),
            check=False,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as error:
        return CheckResult(
            name=check.name,
            returncode=127,
            duration_seconds=time.perf_counter() - started_at,
            output=f"Unable to run uv: {error}\n",
        )

    return CheckResult(
        name=check.name,
        returncode=completed.returncode,
        duration_seconds=time.perf_counter() - started_at,
        output=completed.stdout,
    )


def missing_uv_result(check: CheckDefinition) -> CheckResult:
    """Report a check that could not start because uv is unavailable."""
    return CheckResult(
        name=check.name,
        returncode=127,
        duration_seconds=0.0,
        output="Unable to find the 'uv' executable on PATH.\n",
    )


def first_failure_line(output: str) -> str:
    """Return the first non-empty output line for the summary."""
    for line in output.splitlines():
        if line.strip():
            return line.strip()
    return "(no output)"


def print_report(results: Sequence[CheckResult]) -> None:
    """Print a consolidated validation report."""
    print("Validation summary")
    print("check | status | duration | first line of failure output")
    print("------|--------|----------|-----------------------------")
    for result in results:
        status = "PASS" if result.returncode == 0 else "FAIL"
        failure_line = "-" if result.returncode == 0 else first_failure_line(result.output)
        print(f"{result.name} | {status} | {result.duration_seconds:.2f}s | {failure_line}")

    for result in results:
        if result.returncode == 0:
            continue
        print(f"\nFailure output: {result.name}")
        print("-" * (len(result.name) + 16))
        if result.output:
            print(result.output, end="" if result.output.endswith("\n") else "\n")
        else:
            print("(no output)")


def _tool_version(tool: str, uv_path: str) -> str:
    """Return one tool's self-reported version, or a marker naming why it could not be read."""
    try:
        completed = subprocess.run(
            (uv_path, *UV_RUN_PREFIX, tool, "--version"),
            check=False,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as error:
        return f"unreadable: {error}"
    if completed.returncode != 0:
        return f"unreadable: exit {completed.returncode}"
    return completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "unreadable: no output"


def build_receipt(results: Sequence[CheckResult], uv_path: str, digest: tuple[str, int]) -> dict[str, object]:
    """Build the receipt payload locking this tree to this sweep's result."""
    tree_digest, file_count = digest
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "digest_domain": DIGEST_DOMAIN.decode(),
        "tree_digest": f"sha256:{tree_digest}",
        "digest_file_count": file_count,
        "tools": {
            "python": platform.python_version(),
            "uv": _uv_version(uv_path),
            **{tool: _tool_version(tool, uv_path) for tool in RECORDED_TOOLS},
        },
        "checks": [
            {
                "name": result.name,
                "command": " ".join(check.command),
                "status": "pass" if result.returncode == 0 else "fail",
                "duration_seconds": round(result.duration_seconds, 3),
            }
            for check, result in zip(CHECKS, results, strict=True)
        ],
    }


def _uv_version(uv_path: str) -> str:
    """Return uv's own version; it is invoked directly rather than through `uv run`."""
    try:
        completed = subprocess.run(
            (uv_path, "--version"),
            check=False,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as error:
        return f"unreadable: {error}"
    return completed.stdout.strip() or "unreadable: no output"


def _digest_pathspec() -> tuple[str, ...]:
    """Return the pathspec that limits every git query to the digest inputs."""
    return ("--", *DIGEST_DIRECTORIES, *DIGEST_FILES)


def _git_output(arguments: Sequence[str], service_root: Path, request: bytes | None = None) -> bytes:
    """Run one read-only git command from the service root and return its raw stdout."""
    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=service_root,
            check=False,
            shell=False,
            input=request,
            capture_output=True,
        )
    except OSError as error:
        raise GitQueryError(f"unable to run git: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip() or f"exit {completed.returncode}"
        rendered = " ".join(arguments)
        raise GitQueryError(f"`git {rendered}` failed in {service_root}: {detail}")
    return completed.stdout


def _listed_paths(arguments: Sequence[str], service_root: Path) -> tuple[str, ...]:
    """Return one NUL-separated `git ls-files` listing, keeping only paths the digest covers."""
    listing = _git_output(("ls-files", "-z", *arguments, *_digest_pathspec()), service_root)
    names = (record.decode("utf-8") for record in listing.split(b"\x00") if record)
    return tuple(sorted(name for name in names if is_digest_input(name)))


def _staged_blob_ids(service_root: Path, *, git_output: GitOutputReader = _git_output) -> dict[str, str]:
    """Return the blob id git has staged for every digest input, keyed by relative path.

    Raises when a digest input has an unresolved merge conflict. A clean index carries one stage-0
    record per path; a conflicted path instead carries stages 1 (ancestor), 2 (ours) and 3 (theirs)
    and no stage-0 record at all. Keying a dict by path alone would let whichever record `git
    ls-files` happened to print last silently stand in for "the" staged content, certifying one
    arbitrary side of the conflict as this tree's bytes.
    """
    listing = git_output(("ls-files", "-s", "-z", *_digest_pathspec()), service_root)
    staged: dict[str, str] = {}
    unmerged: set[str] = set()
    for record in listing.split(b"\x00"):
        if not record:
            continue
        metadata, separator, relative = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) < INDEX_ENTRY_FIELD_COUNT:
            continue
        name = relative.decode("utf-8")
        if not is_digest_input(name):
            continue
        if fields[2] != b"0":
            unmerged.add(name)
            continue
        staged[name] = fields[1].decode("utf-8")
    if unmerged:
        paths = ", ".join(sorted(unmerged))
        raise GitQueryError(f"unmerged; resolve the conflict before writing a receipt: {paths}")
    return staged


def _read_blobs(blob_ids: Mapping[str, str], service_root: Path) -> dict[str, bytes]:
    """Return the staged bytes of every named blob, read with one `git cat-file --batch`.

    One batch rather than one process per file: the digest covers over a thousand inputs, and a
    thousand git invocations would make the guard slower than the sweep it protects.
    """
    if not blob_ids:
        return {}
    ordered = sorted(blob_ids)
    request = "".join(f"{blob_ids[relative]}\n" for relative in ordered).encode("utf-8")
    payload = _git_output(("cat-file", "--batch"), service_root, request=request)
    contents: dict[str, bytes] = {}
    offset = 0
    for relative in ordered:
        header_end = payload.find(b"\n", offset)
        if header_end < 0:
            raise GitQueryError(f"`git cat-file --batch` ended before it answered for {relative}")
        header = payload[offset:header_end].decode("utf-8", "replace")
        fields = header.split()
        if len(fields) < BATCH_HEADER_FIELD_COUNT:
            raise GitQueryError(f"git has no staged blob for {relative}: {header}")
        size = int(fields[2])
        start = header_end + 1
        contents[relative] = payload[start : start + size]
        offset = start + size + 1
    return contents


def _git_index_snapshot(service_root: Path) -> IndexSnapshot:
    """Read git's staged view of the digest inputs: their bytes, plus what it will not carry."""
    return IndexSnapshot(
        blobs=_read_blobs(_staged_blob_ids(service_root), service_root),
        untracked=_listed_paths(("--others", "--exclude-standard"), service_root),
        ignored=_listed_paths(("--others", "--ignored", "--exclude-standard"), service_root),
    )


def compare_tree_to_index(service_root: Path, snapshot: IndexSnapshot) -> CommittedTreeVerdict:
    """Return how this working tree's digest inputs differ from the bytes git has staged."""

    def has_unstaged_edit(relative: str) -> bool:
        """Return whether the file on disk differs from the bytes git holds for it."""
        on_disk_content = normalize_content((service_root / relative).read_bytes())
        return on_disk_content != normalize_content(snapshot.blobs[relative])

    on_disk = {path.relative_to(service_root).as_posix() for path in digest_input_paths(service_root)}
    staged = set(snapshot.blobs)
    ignored = tuple(sorted(on_disk & set(snapshot.ignored)))
    return CommittedTreeVerdict(
        untracked=tuple(sorted((on_disk & set(snapshot.untracked)) - set(ignored))),
        modified=tuple(sorted(relative for relative in staged & on_disk if has_unstaged_edit(relative))),
        missing_from_disk=tuple(sorted(staged - on_disk)),
        ignored=ignored,
        disk_digest=compute_tree_digest(service_root),
        index_digest=compute_digest(staged, lambda relative: snapshot.blobs[relative]),
    )


def _describe_group(title: str, paths: Sequence[str], remedy: str) -> list[str]:
    """Return the report lines for one cause, or nothing when that cause did not occur."""
    if not paths:
        return []
    return [f"  {title} -- fix: {remedy}", *(f"    {path}" for path in paths)]


def describe_verdict(verdict: CommittedTreeVerdict) -> str:
    """Return the refusal text naming every disagreement between this tree and git's index."""
    groups = (
        ("untracked, so a fresh checkout would not have it", verdict.untracked, "git add <path>"),
        ("edited since staging, so the digest sees bytes git does not", verdict.modified, "git add <path>"),
        ("staged but missing from the working tree", verdict.missing_from_disk, "git add <path>"),
        ("excluded by an ignore rule, so no checkout can carry it", verdict.ignored, "un-ignore it, or delete it"),
    )
    lines = [
        "Refusing to write a receipt: the digest must describe the bytes git stores, and this tree "
        "disagrees with its index.",
    ]
    for title, paths, remedy in groups:
        lines.extend(_describe_group(title, paths, remedy))
    lines.append(f"  disk:  sha256:{verdict.disk_digest[0]} over {verdict.disk_digest[1]} files")
    lines.append(f"  index: sha256:{verdict.index_digest[0]} over {verdict.index_digest[1]} files")
    return "\n".join(lines)


def _run_selected_checks(
    selected_checks: tuple[CheckDefinition, ...], check_runner: CheckRunner
) -> tuple[str | None, tuple[CheckResult, ...]]:
    """Run every selected check, reporting a missing uv as a failure of each."""
    uv_path = shutil.which("uv")
    if uv_path is None:
        return None, tuple(missing_uv_result(check) for check in selected_checks)
    return uv_path, tuple(check_runner(check, uv_path) for check in selected_checks)


def _write_receipt(
    results: Sequence[CheckResult],
    uv_path: str,
    digest_before: tuple[str, int],
    service_root: Path,
    index_reader: IndexReader,
) -> int:
    """Re-digest the swept tree, refuse whatever a fresh checkout could not reproduce, then write."""
    digest_after = compute_tree_digest(service_root)
    if digest_after != digest_before:
        print(
            "\nRefusing to write a receipt: the tree changed while the sweep ran; re-run.\n"
            f"  before: sha256:{digest_before[0]} over {digest_before[1]} files\n"
            f"  after:  sha256:{digest_after[0]} over {digest_after[1]} files"
        )
        return 1

    try:
        snapshot = index_reader(service_root)
    except GitQueryError as error:
        print(f"\nRefusing to write a receipt: {error}. A receipt only means something for a committed tree.")
        return 1

    verdict = compare_tree_to_index(service_root, snapshot)
    if not verdict.is_clean:
        print("\n" + describe_verdict(verdict))
        return 1

    receipt = build_receipt(results, uv_path, digest_after)
    write_receipt(receipt, service_root / RECEIPT_FILE_NAME)
    print(f"\nWrote {RECEIPT_FILE_NAME}: {receipt['tree_digest']} over {receipt['digest_file_count']} files.")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    service_root: Path = SERVICE_ROOT,
    check_runner: CheckRunner = run_check,
    index_reader: IndexReader = _git_index_snapshot,
) -> int:
    """Run selected validation checks and return a consolidated status."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.list:
        for check in CHECKS:
            print(check.name)
        return 0

    selected_checks = select_checks(arguments.only, parser)
    if arguments.write_receipt and selected_checks != CHECKS:
        print("Refusing to write a receipt: --write-receipt requires every check, not --only.")
        return 1

    # Digested before the sweep as well as after it: a file written during those minutes would
    # otherwise be certified by a run that never read it. `None` exactly when no receipt was asked for.
    digest_before = compute_tree_digest(service_root) if arguments.write_receipt else None
    uv_path, results = _run_selected_checks(selected_checks, check_runner)
    print_report(results)
    failed = any(result.returncode != 0 for result in results)

    if digest_before is None:
        return 1 if failed else 0
    if failed or uv_path is None:
        print("\nRefusing to write a receipt: the sweep was not green.")
        return 1
    return _write_receipt(results, uv_path, digest_before, service_root, index_reader)


if __name__ == "__main__":
    sys.exit(main())
