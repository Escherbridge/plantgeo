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

from quality_receipt import RECEIPT_PATH, RECEIPT_SCHEMA_VERSION, compute_tree_digest, write_receipt

if TYPE_CHECKING:
    from collections.abc import Sequence


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


def build_receipt(results: Sequence[CheckResult], uv_path: str) -> dict[str, object]:
    """Build the receipt payload locking this tree to this sweep's result."""
    tree_digest, file_count = compute_tree_digest()
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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


def main(argv: Sequence[str] | None = None) -> int:
    """Run selected validation checks and return a consolidated status."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.list:
        for check in CHECKS:
            print(check.name)
        return 0

    selected_checks = select_checks(arguments.only, parser)
    uv_path = shutil.which("uv")
    if uv_path is None:
        results = tuple(missing_uv_result(check) for check in selected_checks)
    else:
        results = tuple(run_check(check, uv_path) for check in selected_checks)

    print_report(results)
    failed = any(result.returncode != 0 for result in results)

    if arguments.write_receipt:
        if selected_checks != CHECKS:
            print("\nRefusing to write a receipt: --write-receipt requires every check, not --only.")
            return 1
        if failed or uv_path is None:
            print("\nRefusing to write a receipt: the sweep was not green.")
            return 1
        receipt = build_receipt(results, uv_path)
        write_receipt(receipt)
        print(f"\nWrote {RECEIPT_PATH.name}: {receipt['tree_digest']} over {receipt['digest_file_count']} files.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
