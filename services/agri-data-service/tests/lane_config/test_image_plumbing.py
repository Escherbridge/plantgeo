"""Both images carry every digest input into their receipt stage, and ship `lanes/` beside `src/` (S13, CA4).

`scripts/quality_receipt.py::digest_input_paths` silently skips a directory that is not there, so a
digest input a Dockerfile forgets to COPY, or a `.dockerignore` pattern drops from the context, does
not fail loudly: the receipt stage reports "the tree does not match its receipt", which reads like a
stale receipt. These static checks fail at the sweep instead, naming the missing input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest

from tests.lane_config.builders import SERVICE_ROOT
from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

QUALITY_RECEIPT = load_scripts_module("quality_receipt.py", "quality_receipt")

REPOSITORY_ROOT: Final = SERVICE_ROOT.parents[1]
SERVICE_PREFIX: Final = "services/agri-data-service/"


@dataclass(frozen=True)
class DockerImage:
    """One Dockerfile, its build context, and where the service tree sits inside that context."""

    name: str
    dockerfile: Path
    dockerignore: Path
    context_prefix: str
    receipt_stage: str
    runtime_stage: str


IMAGES: Final = (
    DockerImage(
        name="agri-data-service",
        dockerfile=SERVICE_ROOT / "Dockerfile",
        dockerignore=SERVICE_ROOT / ".dockerignore",
        context_prefix="",
        receipt_stage="quality-receipt",
        runtime_stage="runtime",
    ),
    DockerImage(
        name="job-executor",
        dockerfile=REPOSITORY_ROOT / "infra" / "job-executor" / "Dockerfile",
        dockerignore=REPOSITORY_ROOT / ".dockerignore",
        context_prefix=SERVICE_PREFIX,
        receipt_stage="quality-receipt",
        runtime_stage="stage-1",
    ),
)


@dataclass
class _Stage:
    copies: list[tuple[tuple[str, ...], str]] = field(default_factory=list)


def _instructions(text: str) -> list[str]:
    """Dockerfile instructions with line continuations joined and comments dropped."""
    joined = re.sub(r"\\\n", " ", text)
    return [line.strip() for line in joined.splitlines() if line.strip() and not line.strip().startswith("#")]


def stage_copies(dockerfile: Path) -> dict[str, list[tuple[tuple[str, ...], str]]]:
    """Each stage's build-context COPY instructions as (sources, destination); `--from` copies are skipped.

    A stage is keyed by its `AS` name, or `stage-<index>` when it has none.
    """
    stages: dict[str, _Stage] = {}
    current: _Stage | None = None
    for instruction in _instructions(dockerfile.read_text(encoding="utf-8")):
        keyword, _, rest = instruction.partition(" ")
        if keyword.upper() == "FROM":
            alias = re.search(r"(?i)\sAS\s+(\S+)$", instruction)
            current = stages.setdefault(alias.group(1) if alias else f"stage-{len(stages)}", _Stage())
        elif keyword.upper() == "COPY" and current is not None:
            arguments = rest.split()
            if any(argument.startswith("--from") for argument in arguments):
                continue
            paths = [argument for argument in arguments if not argument.startswith("--")]
            current.copies.append((tuple(paths[:-1]), paths[-1]))
    return {name: stage.copies for name, stage in stages.items()}


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """One `.dockerignore` pattern as a regex over a context-relative POSIX path (Go `filepath.Match` + `**`)."""
    source = pattern.strip().removeprefix("./").lstrip("/")
    regex, index = "", 0
    while index < len(source):
        if source.startswith("**/", index):
            regex, index = regex + "(?:.*/)?", index + 3
        elif source.startswith("**", index):
            regex, index = regex + ".*", index + 2
        elif source[index] == "*":
            regex, index = regex + "[^/]*", index + 1
        elif source[index] == "?":
            regex, index = regex + "[^/]", index + 1
        elif source[index] == "[":
            closing = source.index("]", index)
            regex, index = regex + source[index : closing + 1], closing + 1
        else:
            regex, index = regex + re.escape(source[index]), index + 1
    return re.compile(f"^{regex.rstrip('/')}$")


def ignore_patterns(text: str) -> list[str]:
    """The patterns of one `.dockerignore`, in order, comments and blanks dropped."""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def is_excluded(relative_path: str, patterns: Sequence[str]) -> bool:
    """Docker's rule: the last pattern matching the path or any parent directory decides; `!` re-includes."""
    parts = relative_path.split("/")
    candidates = ["/".join(parts[:count]) for count in range(1, len(parts) + 1)]
    excluded = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        regex = _pattern_regex(pattern.removeprefix("!"))
        if any(regex.match(candidate) for candidate in candidates):
            excluded = not negated
    return excluded


def _copies_into(copies: list[tuple[tuple[str, ...], str]], source: str, destinations: Sequence[str]) -> bool:
    return any(source in sources and destination in destinations for sources, destination in copies)


@pytest.mark.parametrize("image", IMAGES, ids=lambda image: image.name)
def test_the_receipt_stage_copies_every_digest_input(image: DockerImage) -> None:
    """Every `DIGEST_DIRECTORIES` and `DIGEST_FILES` entry, `lanes/` included (CA4), reaches the gate stage."""
    copies = stage_copies(image.dockerfile)[image.receipt_stage]
    missing_directories = [
        directory
        for directory in QUALITY_RECEIPT.DIGEST_DIRECTORIES
        if not _copies_into(copies, f"{image.context_prefix}{directory}/", (f"{directory}/",))
    ]
    missing_files = [
        name
        for name in QUALITY_RECEIPT.DIGEST_FILES
        if not _copies_into(copies, f"{image.context_prefix}{name}", ("./", name))
    ]

    assert missing_directories == []
    assert missing_files == []


@pytest.mark.parametrize("image", IMAGES, ids=lambda image: image.name)
def test_the_runtime_stage_ships_lanes_beside_src(image: DockerImage) -> None:
    """`default_lanes_directory()` finds `lanes/` beside `src/` with no setting only if both land together."""
    copies = stage_copies(image.dockerfile)[image.runtime_stage]

    assert _copies_into(copies, f"{image.context_prefix}src/", ("src/",))
    assert _copies_into(copies, f"{image.context_prefix}lanes/", ("lanes/",))


@pytest.mark.parametrize("image", IMAGES, ids=lambda image: image.name)
def test_no_dockerignore_pattern_drops_a_digest_input_from_the_build_context(image: DockerImage) -> None:
    """Every file the receipt digests on this tree survives the image's own `.dockerignore`."""
    patterns = ignore_patterns(image.dockerignore.read_text(encoding="utf-8"))
    digest_inputs = [path.relative_to(SERVICE_ROOT).as_posix() for path in QUALITY_RECEIPT.digest_input_paths()]
    dropped = [path for path in digest_inputs if is_excluded(f"{image.context_prefix}{path}", patterns)]

    assert any(path.startswith("lanes/") for path in digest_inputs)
    assert dropped == []


@pytest.mark.parametrize(
    ("patterns", "path", "excluded"),
    [
        pytest.param(["lanes"], "lanes/_providers/open-meteo.toml", True, id="a-directory-drops-its-files"),
        pytest.param(["**/*.toml"], "lanes/_providers/open-meteo.toml", True, id="double-star-spans-directories"),
        pytest.param(["*.toml"], "lanes/soil.toml", False, id="single-star-is-root-anchored"),
        pytest.param(["lanes", "!lanes/soil.toml"], "lanes/soil.toml", False, id="a-later-negation-re-includes"),
        pytest.param(["**/__pycache__"], "src/pkg/__pycache__/m.pyc", True, id="nested-cache-directory"),
        pytest.param(["/tmp"], "tmp/scratch.txt", True, id="leading-slash-is-the-context-root"),
        pytest.param(["*.py[cod]"], "module.pyc", True, id="character-class"),
        pytest.param(["infra/*", "!infra/martin/"], "infra/martin/martin.yaml", False, id="root-re-include"),
    ],
)
def test_the_ignore_matcher_reads_patterns_the_way_docker_does(patterns: list[str], path: str, excluded: bool) -> None:
    """The matcher behind the context check, table-driven over the pattern shapes both ignore files use."""
    assert is_excluded(path, patterns) is excluded
