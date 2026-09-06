"""D1 item 2: the repository-wide zero-reader proof, in the c2 removal-packet form.

READS FILES ONLY. Nothing here opens a database, a bucket or a socket, so the proof can be produced
while production is unreachable -- which is the state the tool was built in
(`conductor/tracks/environmental_postgres_retirement_20260904`, wave D).

THE SCAN IS THE AUTHORITY, NOT THE INVENTORY. `evidence/retirement-inventory.md` classes
`agri.spatial_cell` "drop now"; the tree holds five live readers of it. So this module never takes a
classification as evidence of anything -- it walks the surfaces D1 names (the Next.js app, the agent
SQL, Martin's tile functions and its configuration, the CLI, the service and its tests) and reports
what it finds. `packet.py` turns a consumer hit into a refusal whatever the ledger row says.

THE THREE DISPOSITIONS EXIST BECAUSE A GREP CANNOT TELL A READ FROM A DEFINITION. A hit in
`drizzle/0038_tile_low_zoom_routing.sql` may be a tile function SELECTing the relation or the
migration that CREATEd it; both look identical to a substring match. Rather than guess, schema hits
are their own disposition: they never clear a packet on their own and they are listed as objects the
drop migration must itself account for. Documentation hits are recorded and never block -- the c2
form's own rule ("documentation-only hits are recorded but are not consumers",
`conductor/tracks/repository_conformity_hardening_20260901/evidence/removal-proof-packet.md`).

AN EXEMPTION IS ASSERTED, NEVER ABSENT. `sql/agent/feature_value_near_point.sql` keeps a live read of
`geo.features` for `interventions`, a community layer RUNBOOK 0.26.1 keeps in PostgreSQL permanently.
The wave-C adversarial review's complaint about the existing guard test was precisely that it passed
by NOT listing the relation. Here the exemption names the path, the reason and the drop forms it
applies to, and an exemption that matches no hit is reported as stale rather than silently carried.

A COMMENT NAMING A RELATION IS NOT A READ OF IT. `src/lib/server/db/schema.ts` had its Drizzle
declaration of `public.drought_data` removed and replaced with a `//` comment explaining the removal
-- correct practice that this scan used to punish, by counting the comment's own mention of the table
name as the very reference it was announcing the absence of. `_match_lines` now re-tests a match
against `_code_only_lines`' comment-stripped view of the same line; a match that survives only in the
stripped-away portion is DOCUMENTATION regardless of which surface it landed on, and a match that
still appears in what is left is whatever the surface says, so `const x = droughtData; // legacy`
still blocks. It is a heuristic, not a parser, and says so at `_code_only_lines`.

THE STRIPPER CARRIES STATE ACROSS LINES, BUT NEVER GUESSES. The first version of that heuristic saw
one line at a time, so a `/** ... */` JSDoc block and a Python docstring both read as code from the
second line onward -- which cost four false blockers at once (`src/lib/server/services/usda-soil.ts`
:1057 and :1159 are prose explaining why the soil-survey matviews were deliberately NOT repointed,
and `warehouse/parquet/tiers.py:15` is a module docstring explaining the same thing for the
warehouse). `_code_only_lines` now walks a whole file with a one-slot state machine, so a multi-line
comment is stripped from every line it covers. The asymmetry the line-at-a-time version was built on
SURVIVES the change: state is entered only when the opener OWNS ITS LINE and its closer provably
appears further down the same file, a Python triple quote is prose only in DOCSTRING POSITION, and
every case that fails either test stays code. A false block costs a human one cited line to read; a
false clear authorises dropping a relation something still reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

#: Directory names never walked, at any depth. Vendored trees and caches hold copies of the source
#: that would multiply every hit count without adding a single real consumer.
EXCLUDED_DIRECTORY_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)

#: How much of a matched line a packet quotes. Long enough to read the statement, short enough that a
#: relation with 400 hits still renders as a document a person can scroll.
MATCH_EXCERPT_LIMIT: Final = 160

#: Files that are markers of the repository root, used to refuse a root that is not one.
REPOSITORY_ROOT_MARKERS: Final[tuple[str, ...]] = ("drizzle", "services/agri-data-service", "src")


class ReaderScanError(RuntimeError):
    """Raised when the scan cannot be performed at all -- a bad root, an unreadable surface."""


class ReaderDisposition(StrEnum):
    """What one hit means for a drop decision."""

    #: Live code that would break. Blocking.
    CONSUMER = "consumer"
    #: DDL that declares or migrates the relation. Not proof of a read, not proof of its absence
    #: either: the drop migration has to account for it, so it is surfaced rather than dropped.
    SCHEMA_DEFINITION = "schema_definition"
    #: Prose. Recorded, never blocking.
    DOCUMENTATION = "documentation"
    #: A consumer that is a recorded, reasoned exception for this drop form.
    EXEMPT = "exempt"


@dataclass(frozen=True, slots=True)
class ReaderSurface:
    """One searched region of the tree, and what a hit inside it means."""

    name: str
    #: Repository-relative POSIX path. The FIRST surface whose root prefixes a file claims it, so
    #: narrower roots must be ordered before the wider roots that contain them.
    root: str
    suffixes: frozenset[str]
    disposition: ReaderDisposition
    why: str


#: Ordered narrowest-first. `src/__tests__` precedes `src`; `sql/agent` precedes `sql`, which
#: precedes the service's Python tree. Reordering this tuple changes which surface a file is
#: attributed to, so the order is part of the contract, not a formatting detail.
SCAN_SURFACES: Final[tuple[ReaderSurface, ...]] = (
    ReaderSurface(
        name="nextjs_tests",
        root="src/__tests__",
        suffixes=frozenset({".ts", ".tsx"}),
        disposition=ReaderDisposition.CONSUMER,
        why="D1 item 2 names tests explicitly; a suite that pins a relation name is a reference",
    ),
    ReaderSurface(
        name="nextjs_app",
        root="src",
        suffixes=frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}),
        disposition=ReaderDisposition.CONSUMER,
        why="the Next.js app: read model, tRPC routers, API routes, map components",
    ),
    ReaderSurface(
        name="agent_sql",
        root="services/agri-data-service/src/agri_data_service/sql/agent",
        suffixes=frozenset({".sql"}),
        disposition=ReaderDisposition.CONSUMER,
        why="the agent tool statements D1 item 2 names as their own surface",
    ),
    ReaderSurface(
        name="service_sql",
        root="services/agri-data-service/src/agri_data_service/sql",
        suffixes=frozenset({".sql"}),
        disposition=ReaderDisposition.CONSUMER,
        why="every other extracted statement: ingest, pipeline, execution, routes, jobs",
    ),
    ReaderSurface(
        name="cli",
        root="services/agri-data-service/src/agri_data_service/interface",
        suffixes=frozenset({".py"}),
        disposition=ReaderDisposition.CONSUMER,
        why="the CLI surface D1 item 2 names",
    ),
    ReaderSurface(
        name="retirement_tooling",
        root="services/agri-data-service/src/agri_data_service/retirement",
        suffixes=frozenset({".py"}),
        disposition=ReaderDisposition.DOCUMENTATION,
        why=(
            "the drop-packet machinery names relations in order to REASON about them, and would "
            "otherwise report itself as a reader of every relation it ledgers. The exemption is earned, "
            "not assumed: this package opens no database, bucket or socket, which "
            "tests/retirement/test_readers.py asserts against the imports rather than trusting a comment"
        ),
    ),
    ReaderSurface(
        name="drop_packet_script",
        root="services/agri-data-service/scripts/build_drop_packet.py",
        suffixes=frozenset({".py"}),
        disposition=ReaderDisposition.DOCUMENTATION,
        why="the same self-reference, for the operator entry point that drives the package",
    ),
    ReaderSurface(
        name="retirement_tooling_tests",
        root="services/agri-data-service/tests/retirement",
        suffixes=frozenset({".py"}),
        disposition=ReaderDisposition.DOCUMENTATION,
        why=(
            "the same self-reference again: these suites name `agri.spatial_cell` and `geo.features` to "
            "PROVE the refusals fire, and counting them as readers would make every packet report its "
            "own tests as the thing blocking it"
        ),
    ),
    ReaderSurface(
        name="drop_packet_script_tests",
        root="services/agri-data-service/tests/scripts/test_build_drop_packet.py",
        suffixes=frozenset({".py"}),
        disposition=ReaderDisposition.DOCUMENTATION,
        why="the same self-reference, for the operator surface's own suite",
    ),
    ReaderSurface(
        name="service_python",
        root="services/agri-data-service/src",
        suffixes=frozenset({".py"}),
        disposition=ReaderDisposition.CONSUMER,
        why="the service itself: pipeline, execution, jobs, agent, routes, planes, warehouse",
    ),
    ReaderSurface(
        name="service_tests",
        root="services/agri-data-service/tests",
        suffixes=frozenset({".py", ".sql"}),
        disposition=ReaderDisposition.CONSUMER,
        why="a test that names the relation is a reference criterion 4 counts",
    ),
    ReaderSurface(
        name="service_scripts",
        root="services/agri-data-service/scripts",
        suffixes=frozenset({".py"}),
        disposition=ReaderDisposition.CONSUMER,
        why="operator scripts run against production and break exactly like service code",
    ),
    ReaderSurface(
        name="declarative_schema",
        root="services/agri-data-service/db",
        suffixes=frozenset({".sql"}),
        disposition=ReaderDisposition.SCHEMA_DEFINITION,
        why="the declarative agri tree; regenerated after a drop rather than blocking one",
    ),
    ReaderSurface(
        name="alembic",
        root="services/agri-data-service/alembic",
        suffixes=frozenset({".py", ".sql"}),
        disposition=ReaderDisposition.SCHEMA_DEFINITION,
        why="migration history; the drop lands here rather than being blocked by it",
    ),
    ReaderSurface(
        name="martin_config",
        root="infra/martin",
        suffixes=frozenset({".yaml", ".yml", ".toml", ".conf", ".json"}),
        disposition=ReaderDisposition.CONSUMER,
        why="a tile function registered here is published and live, whatever else references it",
    ),
    ReaderSurface(
        name="infra",
        root="infra",
        suffixes=frozenset({".sql", ".yaml", ".yml", ".sh", ".conf"}),
        disposition=ReaderDisposition.CONSUMER,
        why="init scripts and service configuration that run against the database",
    ),
    ReaderSurface(
        name="drizzle",
        root="drizzle",
        suffixes=frozenset({".sql", ".json"}),
        disposition=ReaderDisposition.SCHEMA_DEFINITION,
        why="migrations AND Martin's tile function bodies live here; a hit is one or the other",
    ),
    ReaderSurface(
        name="docs",
        root="docs",
        suffixes=frozenset({".md"}),
        disposition=ReaderDisposition.DOCUMENTATION,
        why="prose",
    ),
    ReaderSurface(
        name="service_docs",
        root="services/agri-data-service/docs",
        suffixes=frozenset({".md"}),
        disposition=ReaderDisposition.DOCUMENTATION,
        why="prose",
    ),
    ReaderSurface(
        name="conductor",
        root="conductor",
        suffixes=frozenset({".md", ".json"}),
        disposition=ReaderDisposition.DOCUMENTATION,
        why="track evidence; the packet's own citations land here and must never block it",
    ),
)


@dataclass(frozen=True, slots=True)
class SearchTerm:
    """One literal string whose presence is evidence of a reference, and why it is."""

    pattern: str
    why: str


@dataclass(frozen=True, slots=True)
class ReaderExemption:
    """A consumer hit that is a recorded, reasoned exception rather than a blocker."""

    path: str
    reason: str
    #: Named by their `DropForm` values rather than the enum, so `ledger.py` may import this module
    #: without `ledger.py`'s own enum having to exist first.
    applies_to_forms: frozenset[str]


@dataclass(frozen=True, slots=True)
class ReaderHit:
    """One matched line: where, in which surface, on which term, and what it says."""

    path: str
    line: int
    surface: str
    disposition: ReaderDisposition
    term: str
    excerpt: str
    exemption_reason: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        """Render one hit for the packet's JSON body."""
        rendered: dict[str, object] = {
            "citation": f"{self.path}:{self.line}",
            "surface": self.surface,
            "disposition": str(self.disposition),
            "term": self.term,
            "excerpt": self.excerpt,
        }
        if self.exemption_reason is not None:
            rendered["exemption_reason"] = self.exemption_reason
        return rendered


@dataclass(frozen=True, slots=True)
class ReaderScan:
    """The whole zero-reader proof for one relation under one drop form."""

    relation: str
    terms: tuple[SearchTerm, ...]
    surfaces_scanned: tuple[str, ...]
    files_scanned: int
    hits: tuple[ReaderHit, ...]
    #: Exemptions that matched nothing. A stale exemption is a claim about the tree that is no longer
    #: true, and carrying one silently is how the next reader inherits a lie.
    unused_exemptions: tuple[ReaderExemption, ...]

    def _by(self, disposition: ReaderDisposition) -> tuple[ReaderHit, ...]:
        """Return every hit with one disposition, in scan order."""
        return tuple(hit for hit in self.hits if hit.disposition is disposition)

    @property
    def consumers(self) -> tuple[ReaderHit, ...]:
        """Live references that block a drop."""
        return self._by(ReaderDisposition.CONSUMER)

    @property
    def schema_definitions(self) -> tuple[ReaderHit, ...]:
        """DDL references the drop migration must account for."""
        return self._by(ReaderDisposition.SCHEMA_DEFINITION)

    @property
    def documentation(self) -> tuple[ReaderHit, ...]:
        """Prose references, recorded and never blocking."""
        return self._by(ReaderDisposition.DOCUMENTATION)

    @property
    def exempt(self) -> tuple[ReaderHit, ...]:
        """Consumer references that are recorded, reasoned exceptions for this drop form."""
        return self._by(ReaderDisposition.EXEMPT)

    @property
    def zero_readers(self) -> bool:
        """True only when no unexempted consumer reference survives anywhere in the tree."""
        return not self.consumers

    def consumer_paths(self) -> tuple[str, ...]:
        """Distinct consumer files, in first-seen order -- the list a refusal names."""
        seen: list[str] = []
        for hit in self.consumers:
            if hit.path not in seen:
                seen.append(hit.path)
        return tuple(seen)

    def to_json_dict(self, *, sample_limit: int) -> dict[str, object]:
        """Render the proof, quoting a bounded sample of each disposition."""
        return {
            "form": "c2-removal-packet",
            "terms": [{"pattern": term.pattern, "why": term.why} for term in self.terms],
            "surfaces_scanned": list(self.surfaces_scanned),
            "files_scanned": self.files_scanned,
            "zero_readers": self.zero_readers,
            "consumer_count": len(self.consumers),
            "consumer_files": list(self.consumer_paths()),
            "consumer_sample": [hit.to_json_dict() for hit in self.consumers[:sample_limit]],
            "exempt_count": len(self.exempt),
            "exempt_sample": [hit.to_json_dict() for hit in self.exempt[:sample_limit]],
            "schema_definition_count": len(self.schema_definitions),
            "schema_definition_sample": [hit.to_json_dict() for hit in self.schema_definitions[:sample_limit]],
            "documentation_count": len(self.documentation),
            "unused_exemptions": [
                {"path": exemption.path, "reason": exemption.reason} for exemption in self.unused_exemptions
            ],
        }


def find_repository_root(start: Path | None = None) -> Path:
    """Walk upwards to the checkout root, refusing anything that does not carry the marker paths.

    A scan rooted one directory too high or too low silently reports zero readers, which is the one
    wrong answer this tool must never produce quietly.
    """
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if all((candidate / marker).exists() for marker in REPOSITORY_ROOT_MARKERS):
            return candidate
    raise ReaderScanError(
        f"no repository root above {here}: expected a directory holding all of {REPOSITORY_ROOT_MARKERS}"
    )


def default_search_terms(relation: str) -> tuple[SearchTerm, ...]:
    """Build the three spellings every relation is referenced by, before any per-relation additions.

    The BARE name is searched deliberately, even though it over-matches: a false blocker costs an
    operator one read of a cited line, while a missed reader costs an irreversible drop. The packet
    prints every hit with its line, so an over-match is visible rather than mysterious.
    """
    schema, separator, bare = relation.partition(".")
    if not separator:
        return (SearchTerm(pattern=relation, why="unqualified object name"),)
    return (
        SearchTerm(pattern=relation, why="schema-qualified reference"),
        SearchTerm(pattern=f'"{schema}"."{bare}"', why="fully quoted identifier"),
        SearchTerm(pattern=f'"{bare}"', why="quoted object name"),
        SearchTerm(pattern=bare, why="bare object name; over-matches on purpose, every hit is cited"),
    )


def _iter_surface_files(root: Path, surface: ReaderSurface, claimed: set[Path]) -> Iterator[Path]:
    """Yield every unclaimed file under one surface, skipping vendored and cache directories.

    A surface root may name a single FILE as well as a directory, which is how one module inside a
    wider surface is given its own disposition without carving a directory out for it.
    """
    surface_root = root / surface.root
    if surface_root.is_file():
        resolved = surface_root.resolve()
        if surface_root.suffix in surface.suffixes and resolved not in claimed:
            claimed.add(resolved)
            yield surface_root
        return
    if not surface_root.is_dir():
        return
    for path in sorted(surface_root.rglob("*")):
        if not path.is_file() or path.suffix not in surface.suffixes:
            continue
        if any(part in EXCLUDED_DIRECTORY_NAMES for part in path.parts):
            continue
        resolved = path.resolve()
        if resolved in claimed:
            continue
        claimed.add(resolved)
        yield path


@dataclass(frozen=True, slots=True)
class _CommentSyntax:
    """The comment markers one file suffix uses, for the heuristic below.

    `line_markers` are checked in the order given; the EARLIEST one found on a line wins, because a
    line can only ever be commented from its first marker onward. `block` is the paired
    open/close form (`/* */`), or `None` for a language that has none. `docstring_delimiters` are
    self-closing delimiters that are prose ONLY in docstring position -- Python's triple quotes,
    which are otherwise ordinary string literals and must keep counting as code. The two are separate
    fields rather than one because they obey different rules, and collapsing them would silently give
    a SQL string literal the licence a docstring earns.
    """

    line_markers: tuple[str, ...]
    block: tuple[str, str] | None
    docstring_delimiters: tuple[str, ...] = ()


#: Comment syntax by file suffix, for the surfaces this repository actually scans. A suffix absent
#: from this mapping gets no comment awareness at all -- every match on it is still counted as code,
#: which is the safe default `_code_only_lines` falls back to.
_COMMENT_SYNTAX_BY_SUFFIX: Final[dict[str, _CommentSyntax]] = {
    ".ts": _CommentSyntax(line_markers=("//",), block=("/*", "*/")),
    ".tsx": _CommentSyntax(line_markers=("//",), block=("/*", "*/")),
    ".js": _CommentSyntax(line_markers=("//",), block=("/*", "*/")),
    ".jsx": _CommentSyntax(line_markers=("//",), block=("/*", "*/")),
    ".mjs": _CommentSyntax(line_markers=("//",), block=("/*", "*/")),
    ".cjs": _CommentSyntax(line_markers=("//",), block=("/*", "*/")),
    ".py": _CommentSyntax(line_markers=("#",), block=None, docstring_delimiters=('"""', "'''")),
    ".yaml": _CommentSyntax(line_markers=("#",), block=None),
    ".yml": _CommentSyntax(line_markers=("#",), block=None),
    ".sql": _CommentSyntax(line_markers=("--",), block=("/*", "*/")),
}

#: The one line a Python docstring may follow: a `def`/`class` header, or the `) -> X:` that closes a
#: signature written across several lines. Anything else -- a `SQL = (` continuation, a dict key, an
#: operator -- leaves a line-initial triple quote classed as DATA, which is the safe answer, because a
#: multi-line SQL literal naming a relation genuinely is a read of it. A module docstring is the other
#: admitted position and is recognised separately, by there being no earlier code line at all.
_PYTHON_DOCSTRING_OWNER: Final = re.compile(r"^(?:async\s+def|def|class)\b.*:$|^\).*:$")


def _closer_for(opener: str, syntax: _CommentSyntax) -> str:
    """Return the marker that ends a region this opener began; a docstring quote closes with itself."""
    if syntax.block is not None and opener == syntax.block[0]:
        return syntax.block[1]
    return opener


def _earliest_opener(line: str, syntax: _CommentSyntax, *, from_index: int) -> tuple[str, int] | None:
    """Return the first multi-line-capable opener at or after `from_index`, and where it starts."""
    openers = [*([] if syntax.block is None else [syntax.block[0]]), *syntax.docstring_delimiters]
    found = [(position, opener) for opener in openers if (position := line.find(opener, from_index)) != -1]
    if not found:
        return None
    position, opener = min(found)
    return opener, position


def _appears_after(lines: Sequence[str], index: int, marker: str) -> bool:
    """True when `marker` occurs on some line after `index` -- the proof an opener really is closed.

    An opener whose closer is nowhere below it is not a comment spanning lines; it is an unterminated
    marker, or a `/*` sitting inside a string. Requiring the closer keeps such a line from silently
    swallowing the rest of the file into "comment" and clearing every relation named in it.
    """
    return any(marker in line for line in lines[index + 1 :])


def _strip_comment_regions(
    lines: Sequence[str],
    index: int,
    syntax: _CommentSyntax,
    *,
    start: int,
    docstring_allowed: bool,
) -> tuple[str, str | None]:
    """Return `(the code-only view of lines[index] from `start`, the closer now awaited)`.

    `start` is 0 for an ordinary line, or the offset just past the closer on a line that ENDED a
    multi-line comment; everything before it was comment and contributes nothing. Indices below are
    absolute in the line, so "the opener owns this line" is asked of the real line rather than of a
    shrinking tail.
    """
    pieces: list[str] = []
    line = lines[index]
    cursor = start
    while True:
        opener_at = _earliest_opener(line, syntax, from_index=cursor)
        if opener_at is None:
            pieces.append(line[cursor:])
            return "".join(pieces), None
        opener, position = opener_at
        owns_the_line = start == 0 and not line[:position].strip()
        if opener in syntax.docstring_delimiters and not (docstring_allowed and owns_the_line):
            # A triple-quoted STRING rather than a docstring, so the rest of the line stays code and
            # no state is entered: a SQL literal that names a relation is a read of that relation.
            pieces.append(line[cursor:])
            return "".join(pieces), None
        closer = _closer_for(opener, syntax)
        end = line.find(closer, position + len(opener))
        if end != -1:
            pieces.append(line[cursor:position])
            cursor = end + len(closer)
            continue
        if owns_the_line and _appears_after(lines, index, closer):
            pieces.append(line[cursor:position])
            return "".join(pieces), closer
        # Unclosed and not provably a comment -- an unmatched opener, or one sitting mid-line inside
        # something this heuristic cannot read. Keep it as code rather than guess where it ends.
        pieces.append(line[cursor:])
        return "".join(pieces), None


def _strip_line_comment(code: str, syntax: _CommentSyntax) -> str:
    """Cut one line's code at its earliest line-comment marker."""
    earliest = min(
        (position for marker in syntax.line_markers if (position := code.find(marker)) != -1),
        default=None,
    )
    return code if earliest is None else code[:earliest]


def _code_only_lines(lines: Sequence[str], syntax: _CommentSyntax | None) -> list[str]:
    """Return every line's NOT-inside-a-comment view, carrying block state across lines.

    This is deliberately not a parser. It knows nothing about string literals, so a line-comment
    marker or a block-comment opener appearing inside a same-line string (a URL's `//`, a SQL
    literal's `--`) is still read as the start of a comment.

    WHAT IT CAN NOW SEE that the line-at-a-time version could not: a block comment opened on an
    EARLIER line (the `/** ... */` JSDoc shape), and a Python module, class or function docstring.
    Both are entered only on proof rather than on a guess -- the opener must own its line, and its
    closer must appear somewhere below it in the same file.

    WHAT IT STILL CANNOT SEE, and deliberately resolves toward "code" in every case:
      * a `/*` or a triple quote that owns its line but sits inside a multi-line string literal -- a
        SQL block comment inside a TypeScript template literal is the realistic shape, and it would
        be stripped;
      * a Python triple quote in a position this module refuses to call a docstring (a `SQL = (`
        continuation, a dict value, or an `r`/`f`-prefixed triple quote, whose first non-whitespace
        character is the prefix letter rather than the quote), which stays code -- so a docstring
        written that way is a FALSE BLOCK, one cited line for a human to dismiss;
      * a match inside a multi-line string in any language, which stays code for the same reason;
      * an unterminated opener anywhere, which stays code because `_appears_after` refuses it.
    """
    if syntax is None:
        return list(lines)
    visible: list[str] = []
    awaiting: str | None = None
    seen_a_code_line = False
    previous_code_line = ""
    for index, line in enumerate(lines):
        if awaiting is not None:
            end = line.find(awaiting)
            if end == -1:
                visible.append("")
                continue
            resume_at = end + len(awaiting)
            awaiting = None
            code, awaiting = _strip_comment_regions(lines, index, syntax, start=resume_at, docstring_allowed=False)
        else:
            # A docstring may open where nothing has come before it (the module docstring) or
            # directly under a `def`/`class` header. `previous_code_line` is taken from the STRIPPED
            # view, so blank lines, `#` comments and decorated headers all resolve correctly.
            docstring_allowed = not seen_a_code_line or _PYTHON_DOCSTRING_OWNER.match(previous_code_line) is not None
            code, awaiting = _strip_comment_regions(lines, index, syntax, start=0, docstring_allowed=docstring_allowed)
        code = _strip_line_comment(code, syntax)
        visible.append(code)
        if code.strip():
            seen_a_code_line = True
            previous_code_line = code.strip()
    return visible


def _match_lines(
    text: str, terms: Sequence[SearchTerm], syntax: _CommentSyntax | None
) -> Iterator[tuple[int, SearchTerm, str, bool]]:
    """Yield `(line number, term, excerpt, comment_only)` ONCE per matching line, most-specific term first.

    One hit per line, not one per term: `geo.mv_soil_survey_grid` and the bare `mv_soil_survey_grid`
    both match the same line, and counting it twice doubles every consumer figure a reader is asked
    to act on. `default_search_terms` orders qualified before bare, so the first match is the most
    specific spelling present.

    `comment_only` is true when the matched term appears in the full line but NOT in
    `_code_only_lines`' view of it -- the match exists only inside a comment. A line that carries the
    term in both code and a trailing comment (`const x = droughtData; // legacy`) matches in the code
    portion too, so `comment_only` is false and the hit still counts as whatever the surface says.

    The whole-file strip is skipped for a file no term appears in at all, which is nearly every file
    of the ~2,000 scanned; the state machine only ever runs where its answer can change a hit.
    """
    lowered_text = text.lower()
    if not any(term.pattern.lower() in lowered_text for term in terms):
        return
    lines = text.splitlines()
    visible = _code_only_lines(lines, syntax)
    for index, (line, code) in enumerate(zip(lines, visible, strict=True), start=1):
        lowered = line.lower()
        for term in terms:
            pattern = term.pattern.lower()
            if pattern in lowered:
                comment_only = pattern not in code.lower()
                yield index, term, line.strip()[:MATCH_EXCERPT_LIMIT], comment_only
                break


def _exemption_for(
    relative_path: str, exemptions: Iterable[ReaderExemption], *, drop_form: str
) -> ReaderExemption | None:
    """Return the exemption covering one file under one drop form, or `None`.

    The form is part of the match: `feature_value_near_point.sql` is an exception to deleting the
    seven environmental layers' ROWS from `geo.features`, and is emphatically not an exception to
    dropping the table it reads.
    """
    for exemption in exemptions:
        if exemption.path == relative_path and drop_form in exemption.applies_to_forms:
            return exemption
    return None


def scan_for_readers(  # noqa: PLR0913 - each argument is one independent coordinate of the scan
    *,
    relation: str,
    terms: Sequence[SearchTerm],
    drop_form: str,
    exemptions: Sequence[ReaderExemption] = (),
    repository_root: Path | None = None,
    surfaces: Sequence[ReaderSurface] = SCAN_SURFACES,
) -> ReaderScan:
    """Walk every named surface once and return the counted, cited reference proof.

    Each file is attributed to exactly ONE surface -- the first whose root prefixes it -- so a
    statement under `sql/agent/` is reported as agent SQL rather than counted twice as service SQL.
    """
    root = repository_root or find_repository_root()
    if not root.is_dir():
        raise ReaderScanError(f"repository root {root} is not a directory")
    claimed: set[Path] = set()
    hits: list[ReaderHit] = []
    exempted_paths: set[str] = set()
    files_scanned = 0
    for surface in surfaces:
        for path in _iter_surface_files(root, surface, claimed):
            files_scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                raise ReaderScanError(f"could not read {path}: {type(error).__name__}: {error}") from error
            relative = path.relative_to(root).as_posix()
            exemption = (
                _exemption_for(relative, exemptions, drop_form=drop_form)
                if surface.disposition is ReaderDisposition.CONSUMER
                else None
            )
            syntax = _COMMENT_SYNTAX_BY_SUFFIX.get(path.suffix)
            for line, term, excerpt, comment_only in _match_lines(text, terms, syntax):
                # A match that exists only inside a comment is prose about the relation, not a
                # reference to it, whatever the surface it landed on would otherwise say -- so it
                # is reported and never blocks, exactly like every other documentation hit, and it
                # never reaches the exemption check below (there is no consumer reference here to
                # exempt).
                if comment_only and surface.disposition is not ReaderDisposition.DOCUMENTATION:
                    hits.append(
                        ReaderHit(
                            path=relative,
                            line=line,
                            surface=surface.name,
                            disposition=ReaderDisposition.DOCUMENTATION,
                            term=term.pattern,
                            excerpt=excerpt,
                        )
                    )
                    continue
                if exemption is not None:
                    exempted_paths.add(relative)
                hits.append(
                    ReaderHit(
                        path=relative,
                        line=line,
                        surface=surface.name,
                        disposition=(ReaderDisposition.EXEMPT if exemption is not None else surface.disposition),
                        term=term.pattern,
                        excerpt=excerpt,
                        exemption_reason=None if exemption is None else exemption.reason,
                    )
                )
    unused = tuple(
        exemption
        for exemption in exemptions
        if drop_form in exemption.applies_to_forms and exemption.path not in exempted_paths
    )
    return ReaderScan(
        relation=relation,
        terms=tuple(terms),
        surfaces_scanned=tuple(surface.name for surface in surfaces),
        files_scanned=files_scanned,
        hits=tuple(hits),
        unused_exemptions=unused,
    )


__all__ = [
    "EXCLUDED_DIRECTORY_NAMES",
    "MATCH_EXCERPT_LIMIT",
    "SCAN_SURFACES",
    "ReaderDisposition",
    "ReaderExemption",
    "ReaderHit",
    "ReaderScan",
    "ReaderScanError",
    "ReaderSurface",
    "SearchTerm",
    "default_search_terms",
    "find_repository_root",
    "scan_for_readers",
]
