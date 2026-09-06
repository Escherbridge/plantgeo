"""The zero-reader proof: what it counts as a consumer, what it exempts, and what it refuses to claim."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.retirement.readers import (
    EXCLUDED_DIRECTORY_NAMES,
    SCAN_SURFACES,
    ReaderDisposition,
    ReaderExemption,
    ReaderScan,
    ReaderScanError,
    SearchTerm,
    default_search_terms,
    find_repository_root,
    scan_for_readers,
)
from tests.retirement import build_checkout

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

#: Deliberately absent from `SYNTHETIC_INVENTORY` (which names `mv_orphan`, not this relation): the
#: inventory file itself is always written under `conductor/`, which the `conductor` scan surface
#: reads as prose, so any relation the inventory happens to mention picks up one free documentation
#: hit before a test adds its own.
_RELATION: Final = "geo.mv_reader_probe"
_TERMS: Final = default_search_terms(_RELATION)

#: Imports that would make the drop-packet machinery capable of touching production. The
#: `retirement_tooling` surface exempts this package from its own scans, and this is what earns it.
_FORBIDDEN_IMPORT_PATTERN: Final = re.compile(
    r"^\s*(?:from|import)\s+(?:sqlalchemy|boto3|botocore|asyncpg|psycopg|httpx|requests|aiohttp|"
    r"agri_data_service\.(?:db|config)\b)",
    re.MULTILINE,
)


def _scan(
    root: Path,
    *,
    form: str = "materialized_view_drop",
    exemptions: Sequence[ReaderExemption] = (),
) -> ReaderScan:
    """Scan one synthetic checkout for the shared fixture relation."""
    return scan_for_readers(
        relation=_RELATION,
        terms=_TERMS,
        drop_form=form,
        exemptions=exemptions,
        repository_root=root,
    )


def test_a_live_reference_in_app_code_is_a_consumer(tmp_path: Path) -> None:
    """The Next.js app is one of the surfaces D1 item 2 names by hand."""
    root = build_checkout(tmp_path, files={"src/lib/server/read-model.ts": "const q = `FROM geo.mv_reader_probe`\n"})

    scan = _scan(root)

    assert scan.zero_readers is False
    assert scan.consumer_paths() == ("src/lib/server/read-model.ts",)
    assert scan.consumers[0].surface == "nextjs_app"


def test_a_migration_hit_is_a_schema_definition_and_does_not_block_alone(tmp_path: Path) -> None:
    """A grep cannot tell a tile function's SELECT from the CREATE beside it, so it claims neither."""
    root = build_checkout(tmp_path, files={"drizzle/0001_x.sql": "CREATE MATERIALIZED VIEW geo.mv_reader_probe AS\n"})

    scan = _scan(root)

    assert scan.zero_readers is True
    assert len(scan.schema_definitions) == 1
    assert scan.schema_definitions[0].disposition is ReaderDisposition.SCHEMA_DEFINITION


def test_prose_is_recorded_and_never_blocks(tmp_path: Path) -> None:
    """The c2 form's own rule: documentation-only hits are recorded but are not consumers."""
    root = build_checkout(tmp_path, files={"docs/notes.md": "we used to read geo.mv_reader_probe\n"})

    scan = _scan(root)

    assert scan.zero_readers is True
    assert len(scan.documentation) == 1


def test_one_line_yields_one_hit_even_when_several_terms_match(tmp_path: Path) -> None:
    """`geo.mv_reader_probe` and the bare `mv_reader_probe` on one line is one reference, not two.

    The line must be actual code, not a comment -- a `//`-prefixed line is exactly the comment-only
    case `test_a_comment_only_reference_is_documentation_not_a_consumer` pins, and would no longer
    land in `scan.consumers` at all.
    """
    root = build_checkout(
        tmp_path, files={"src/a.ts": "const ref = 'geo.mv_reader_probe and mv_reader_probe on one line';\n"}
    )

    scan = _scan(root)

    assert len(scan.consumers) == 1
    assert scan.consumers[0].term == "geo.mv_reader_probe"


def test_a_comment_only_reference_is_documentation_not_a_consumer(tmp_path: Path) -> None:
    """The defect this heuristic fixes: a `//` comment naming the relation is not a read of it.

    This is the real-world shape -- `src/lib/server/db/schema.ts` had `public.drought_data`'s Drizzle
    declaration removed and replaced with a comment naming the table, and the scan matched the
    comment's own mention of the name as if it were the reference it was announcing the absence of.
    """
    root = build_checkout(
        tmp_path,
        files={
            "src/lib/server/db/schema.ts": (
                "// `geo.mv_reader_probe` had its Drizzle declaration removed here -- zero readers left.\n"
            )
        },
    )

    scan = _scan(root)

    assert scan.zero_readers is True
    assert scan.consumers == ()
    assert len(scan.documentation) == 1
    assert scan.documentation[0].path == "src/lib/server/db/schema.ts"
    assert scan.documentation[0].surface == "nextjs_app"


def test_a_code_line_with_a_trailing_comment_is_still_a_consumer(tmp_path: Path) -> None:
    """A reference in the code portion of a line still blocks, whatever a trailing comment adds."""
    root = build_checkout(
        tmp_path,
        files={"src/a.ts": "const rows = db.query('geo.mv_reader_probe'); // legacy path, remove after wave D\n"},
    )

    scan = _scan(root)

    assert scan.zero_readers is False
    assert scan.consumers[0].path == "src/a.ts"
    assert scan.consumers[0].disposition is ReaderDisposition.CONSUMER


def test_a_comment_only_reference_in_python_is_documentation(tmp_path: Path) -> None:
    """The `#` marker required for Python and YAML, on the service's own Python surface."""
    root = build_checkout(
        tmp_path,
        files={
            "services/agri-data-service/src/agri_data_service/pipeline/note.py": (
                "# geo.mv_reader_probe was dropped in wave D; see the retirement track.\n"
            )
        },
    )

    scan = _scan(root)

    assert scan.zero_readers is True
    assert len(scan.documentation) == 1


def test_a_comment_only_reference_in_sql_is_documentation(tmp_path: Path) -> None:
    """The `--` marker required for SQL, on the agent-SQL surface D1 item 2 names by hand."""
    root = build_checkout(
        tmp_path,
        files={
            "services/agri-data-service/src/agri_data_service/sql/agent/note.sql": (
                "-- geo.mv_reader_probe: dropped in wave D, kept here as a pointer\nSELECT 1;\n"
            )
        },
    )

    scan = _scan(root)

    assert scan.zero_readers is True
    assert len(scan.documentation) == 1


def test_a_same_line_block_comment_reference_is_documentation(tmp_path: Path) -> None:
    """The `/* */` marker required for TypeScript/JavaScript, closed on the same line as it opens."""
    root = build_checkout(tmp_path, files={"src/a.ts": "/* geo.mv_reader_probe: removed, see track */\n"})

    scan = _scan(root)

    assert scan.zero_readers is True
    assert len(scan.documentation) == 1


def test_a_block_comment_opener_with_no_closer_below_it_is_not_stripped(tmp_path: Path) -> None:
    """The proof `_appears_after` demands: an opener whose closer is nowhere below it is not a comment.

    It is an unterminated marker, or a `/*` sitting inside a string. Entering comment state on it
    would let one line swallow the rest of a file and clear every relation named in it, so the
    heuristic keeps it as code -- a false block, never a false clear.
    """
    root = build_checkout(tmp_path, files={"src/a.ts": "/* start of a long comment about geo.mv_reader_probe\n"})

    scan = _scan(root)

    assert scan.zero_readers is False
    assert scan.consumers[0].disposition is ReaderDisposition.CONSUMER


def test_a_reference_inside_a_multi_line_jsdoc_block_is_documentation(tmp_path: Path) -> None:
    """The first of the two blind spots that cost four false blockers, in its real shape.

    `src/lib/server/services/usda-soil.ts` explains at :1057 and :1159 why the soil-survey read paths
    were deliberately NOT repointed at `geo.mv_soil_survey_union`/`_grid`. Both explanations sit four
    lines inside a `/** ... */` block, so the line-at-a-time heuristic saw an ordinary line of code
    naming a matview and blocked both drops on the prose announcing that nothing reads them.
    """
    root = build_checkout(
        tmp_path,
        files={
            "src/lib/server/services/usda-soil.ts": (
                "/**\n"
                " * Merges the stored map units intersecting the viewport by drainage class.\n"
                " *\n"
                " * NOT REPOINTED at `geo.mv_reader_probe` in the pre-aggregation pass, and the reason\n"
                " * is a real grain mismatch rather than an omission.\n"
                " */\n"
                "async function readAggregatedFeatures() {\n"
                "  return 1;\n"
                "}\n"
            )
        },
    )

    scan = _scan(root)

    assert scan.zero_readers is True
    assert [hit.line for hit in scan.documentation] == [4]
    assert scan.documentation[0].surface == "nextjs_app"


def test_code_in_a_jsdoc_heavy_file_is_still_a_consumer(tmp_path: Path) -> None:
    """The other direction of the same fix: state is exited at the closer, not carried past it."""
    root = build_checkout(
        tmp_path,
        files={
            "src/lib/server/services/usda-soil.ts": (
                "/**\n"
                " * Prose naming `geo.mv_reader_probe`, which must not block.\n"
                " */\n"
                "const rows = await sql`SELECT zoom_tier FROM geo.mv_reader_probe`;\n"
            )
        },
    )

    scan = _scan(root)

    assert scan.zero_readers is False
    assert [hit.line for hit in scan.consumers] == [4]
    assert [hit.line for hit in scan.documentation] == [2]


def test_a_reference_in_a_python_module_docstring_is_documentation(tmp_path: Path) -> None:
    """The second blind spot: Python was registered with no block form, so a docstring read as code.

    `warehouse/parquet/tiers.py:15` is a module-docstring line explaining why the Parquet warehouse
    derives its coarse rungs from the base Parquet and never from the PostGIS-era matviews -- a third
    false blocker on the same two relations, from prose that says they are not read.
    """
    root = build_checkout(
        tmp_path,
        files={
            "services/agri-data-service/src/agri_data_service/warehouse/parquet/tiers.py": (
                '"""The tier derivation: how one lane\'s base Parquet becomes its coarser rungs.\n'
                "\n"
                "WHY THE COARSE RUNGS ARE DERIVED FROM THE BASE PARQUET AND NEVER FROM POSTGRES.\n"
                "`geo.mv_reader_probe` is the PostGIS era's own per-layer tier, and reading it would make\n"
                "the warehouse depend on the database it is replacing.\n"
                '"""\n'
                "\n"
                "TIER_COUNT = 4\n"
            )
        },
    )

    scan = _scan(root)

    assert scan.zero_readers is True
    assert [hit.line for hit in scan.documentation] == [4]
    assert scan.documentation[0].surface == "service_python"


def test_a_reference_in_a_python_function_docstring_is_documentation(tmp_path: Path) -> None:
    """A docstring is admitted under a `def`/`class` header as well as at the top of a module."""
    root = build_checkout(
        tmp_path,
        files={
            "services/agri-data-service/src/agri_data_service/pipeline/derive.py": (
                "def derive_tier() -> int:\n"
                '    """Derive one coarse rung from the base rung below it.\n'
                "\n"
                "    Never from `geo.mv_reader_probe`: a rung read from the database this replaces can\n"
                "    drift away from the base rung under it.\n"
                '    """\n'
                "    return 4\n"
            )
        },
    )

    scan = _scan(root)

    assert scan.zero_readers is True
    assert [hit.line for hit in scan.documentation] == [4]


def test_a_multi_line_python_string_that_is_not_a_docstring_is_still_a_consumer(tmp_path: Path) -> None:
    """The line that decides whether the docstring rule is safe: a SQL literal IS a read.

    A triple quote owning its line is prose only in DOCSTRING POSITION -- at the top of a module, or
    directly under a `def`/`class` header. Under a `SQL = (` continuation it is data, and data that
    names a relation reads it, so the statement below still blocks.
    """
    root = build_checkout(
        tmp_path,
        files={
            "services/agri-data-service/src/agri_data_service/pipeline/query.py": (
                'COVERAGE_SQL = (\n    """\n    SELECT observed_day FROM geo.mv_reader_probe\n    """\n)\n'
            )
        },
    )

    scan = _scan(root)

    assert scan.zero_readers is False
    assert [hit.line for hit in scan.consumers] == [3]


def test_a_same_line_triple_quoted_python_string_is_still_a_consumer(tmp_path: Path) -> None:
    """A triple quote that does not own its line is a value being assigned, never a docstring."""
    root = build_checkout(
        tmp_path,
        files={
            "services/agri-data-service/src/agri_data_service/pipeline/query.py": (
                'DAY_SQL = """SELECT observed_day FROM geo.mv_reader_probe"""\n'
            )
        },
    )

    scan = _scan(root)

    assert scan.zero_readers is False
    assert [hit.line for hit in scan.consumers] == [1]


def test_a_docstring_opener_with_no_closer_below_it_is_not_stripped(tmp_path: Path) -> None:
    """The same closer proof the block form demands, for the Python form."""
    root = build_checkout(
        tmp_path,
        files={
            "services/agri-data-service/src/agri_data_service/pipeline/truncated.py": (
                '"""An opener that never closes, naming geo.mv_reader_probe.\n'
            )
        },
    )

    scan = _scan(root)

    assert scan.zero_readers is False
    assert [hit.line for hit in scan.consumers] == [1]


def test_an_exemption_marks_a_hit_exempt_only_for_the_form_it_names(tmp_path: Path) -> None:
    """`feature_value_near_point.sql` is an exception to deleting rows, never to dropping the table."""
    root = build_checkout(tmp_path, files={"src/keeper.ts": "FROM geo.mv_reader_probe\n"})
    exemption = ReaderExemption(
        path="src/keeper.ts",
        reason="reads a layer that stays",
        applies_to_forms=frozenset({"row_delete"}),
    )

    exempted = _scan(root, form="row_delete", exemptions=(exemption,))
    unexempted = _scan(root, form="table_drop", exemptions=(exemption,))

    assert exempted.zero_readers is True
    assert exempted.exempt[0].exemption_reason == "reads a layer that stays"
    assert unexempted.zero_readers is False


def test_an_exemption_that_matches_nothing_is_reported_as_stale(tmp_path: Path) -> None:
    """A carried exemption is a claim about the tree; when it stops being true it must be visible."""
    root = build_checkout(tmp_path)
    exemption = ReaderExemption(
        path="src/gone.ts",
        reason="was a reader once",
        applies_to_forms=frozenset({"row_delete"}),
    )

    scan = _scan(root, form="row_delete", exemptions=(exemption,))

    assert scan.unused_exemptions == (exemption,)


def test_a_file_is_attributed_to_exactly_one_surface(tmp_path: Path) -> None:
    """`src/__tests__` precedes `src`, so a suite is never double-counted as app code."""
    root = build_checkout(tmp_path, files={"src/__tests__/a.test.ts": "geo.mv_reader_probe\n"})

    scan = _scan(root)

    assert len(scan.consumers) == 1
    assert scan.consumers[0].surface == "nextjs_tests"


def test_a_single_file_surface_root_is_honoured(tmp_path: Path) -> None:
    """The drop-packet script gets its own disposition without carving a directory out for it."""
    script = "services/agri-data-service/scripts/build_drop_packet.py"
    root = build_checkout(tmp_path, files={script: "geo.mv_reader_probe\n"})

    scan = _scan(root)

    assert scan.zero_readers is True
    assert scan.documentation[0].surface == "drop_packet_script"


def test_a_root_without_the_marker_paths_is_refused(tmp_path: Path) -> None:
    """A scan rooted one directory off reports zero readers, the one wrong answer that must be loud."""
    with pytest.raises(ReaderScanError, match="no repository root"):
        find_repository_root(tmp_path / "nowhere")


def test_a_bare_relation_name_does_not_produce_an_empty_quoted_term() -> None:
    """An unqualified name must yield one usable pattern, never the vacuous `""`."""
    assert [term.pattern for term in default_search_terms("drought_data")] == ["drought_data"]
    assert '""' not in [term.pattern for term in default_search_terms("geo.features")]


def test_the_retirement_package_cannot_reach_production() -> None:
    """This is what earns the `retirement_tooling` self-exemption; it is asserted, not assumed.

    The package exempts itself from its own scans because it NAMES relations in order to reason about
    them. That exemption is only honest while the package has no way to touch a database or a bucket,
    so the property is checked against the imports rather than promised in a comment.
    """
    root = find_repository_root()
    package = root / "services/agri-data-service/src/agri_data_service/retirement"
    script = root / "services/agri-data-service/scripts/build_drop_packet.py"

    offenders = [
        path.name
        for path in [*sorted(package.glob("*.py")), script]
        if _FORBIDDEN_IMPORT_PATTERN.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_every_surface_root_is_distinct_and_narrow_before_wide() -> None:
    """Surface order is contract: a wider root listed first would swallow a narrower one's files."""
    roots = [surface.root for surface in SCAN_SURFACES]

    assert len(roots) == len(set(roots))
    for index, root in enumerate(roots):
        swallowed_by = [other for other in roots[:index] if root.startswith(f"{other}/")]
        assert swallowed_by == [], f"{root} is listed after the wider root(s) {swallowed_by}"


def test_a_search_term_carries_the_reason_it_is_evidence() -> None:
    """A term with no `why` is a grep; a term with one is a proof a reader can audit."""
    assert all(isinstance(term, SearchTerm) and term.why for term in default_search_terms("geo.features"))


# --------------------------------------------------------------------------------------------------
# The tripwire for the one documented path to a false clear: a line-initial `/*` (never `/**`),
# genuinely inside an open backtick template literal, that `_code_only_lines` would treat as OPENING
# a multi-line comment region on a scanned TS/JS surface.
#
# `_appears_after` (readers.py) proves only that a `*/` exists SOMEWHERE below an opener, never that
# it belongs to the same string. So a `/*` that owns its line inside a multi-line backtick template
# literal is read as a real comment, and everything up to the next `*/` anywhere in the file --
# including a genuine `SELECT ... FROM <relation>` on the very next line -- is silently classed as
# DOCUMENTATION. That is a FALSE CLEAR: the one direction that can wrongly authorise a drop, per
# `_code_only_lines`'s own docstring ("A false block costs a human one cited line to read; a false
# clear authorises dropping a relation something still reads", readers.py:36-46) and
# `retirement/AGENTS.md` "The comment stripper carries state across lines, and still refuses to
# guess".
#
# WHY `/**` IS EXCLUDED. JSDoc's `/**` is the ubiquitous, safe form of exactly this opener --
# thousands of instances across the app, none of them inside a template literal. Flagging it would
# make the guard fire immediately and constantly, which is precisely how a guard earns being deleted
# by the first person it annoys. `/*` is single-star specifically because that is the shape the
# review reproduced (`/* fake sql comment inside a template literal`) and the shape nobody writes as
# genuine multi-line documentation in this codebase.
#
# WHY BACKTICK CONTEXT IS CHECKED AFTER ALL. The first version of this guard skipped it, on the
# theory that a line-based backtick count could not be trusted -- and it fired on 22 real lines the
# very first time it ran against this tree, 18 of which were ordinary top-level `/*\n * ...\n */`
# explanatory comments nowhere near a backtick. That is exactly the "annoys the first person" failure
# note 1 warns about, and it is also a shape this codebase writes constantly, so it would recur every
# week. `_is_inside_an_open_backtick_region` fixes that: a cumulative, whole-file count of
# un-escaped backtick characters, checked for oddness before the opener. It was validated by hand
# against every one of those 22 real hits (`git log` / review notes for this change carry the
# reproduction) -- including files like `environmental-read-model.ts` that open and close several
# HUNDRED real backticks building SQL earlier in the file, where the count still correctly lands back
# on "outside a template literal" once those queries close. Its one known blind spot: an UNPAIRED
# backtick hidden inside a string, a line comment, or an already-open block comment earlier in the
# same file would flip this parity for every line after it, in either direction. Getting it wrong in
# the direction that SUPPRESSES a hit is the one mistake this guard cannot afford, which is why it is
# not trusted to clear a hit on its own for the one shape that genuinely sits inside a template
# literal today: see `_ACKNOWLEDGED_TEMPLATE_LITERAL_COMMENTS` below, which is asserted and checked
# for staleness rather than inferred by a second heuristic layered on the first.
# --------------------------------------------------------------------------------------------------

#: TS/JS-family suffixes `_COMMENT_SYNTAX_BY_SUFFIX` gives the `/* */` block form. `.sql` also has a
#: block form but no backtick template literals, and `.py`'s block form is `None` (it uses triple
#: quotes instead), so neither surface can reproduce this shape the way a TS/JS one can.
_JS_LIKE_SUFFIXES: Final = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})

#: A `/*` that owns its line (only whitespace may precede it) and is not `/**`.
_LINE_INITIAL_SINGLE_STAR_OPENER: Final = re.compile(r"^\s*/\*(?!\*)")


def _would_open_a_multiline_comment_region(lines: Sequence[str], index: int) -> bool:
    """True when `lines[index]` is a dangerous opener `_code_only_lines` would actually act on.

    Mirrors `_strip_comment_regions`'s own two admission tests for a multi-line block comment: no
    closer on the SAME line (otherwise it is a single-line block comment that never spans into
    anything below it -- the harmless `{/* ... */}` JSX shape always takes this exit), and a closer
    present somewhere further down the file, exactly as `_appears_after` checks it -- by presence
    alone, not by correspondence, which is the reason an unrelated `*/` later in the file is enough
    to trigger the same false clear the review found.
    """
    line = lines[index]
    match = _LINE_INITIAL_SINGLE_STAR_OPENER.match(line)
    if match is None:
        return False
    after_opener = line[match.end() :]
    if "*/" in after_opener:
        return False  # closes on the same line: a single-line block comment, never spans lines
    return any("*/" in later_line for later_line in lines[index + 1 :])


def _is_inside_an_open_backtick_region(lines: Sequence[str], index: int) -> bool:
    """True when an ODD number of un-escaped backticks appear in `lines[:index]`.

    This is the shape that actually makes a dangerous opener dangerous: without an open template
    literal around it, a line-initial `/*` is either a real comment or, per `_would_open_a_multiline_
    comment_region`'s own no-closer branch, correctly left as code -- neither one is a false clear.
    Cumulative and whole-file on purpose, so a template literal opened many lines above (or many
    backticks earlier) is still tracked; validated by hand against this repository's real content,
    where files with hundreds of genuine, balanced SQL-building backticks earlier in the file still
    correctly resolve to "closed" by the time an unrelated later comment is reached. See the module
    note above for the one thing it cannot see: an unpaired backtick trapped inside a string or an
    earlier comment would flip every line after it, which is exactly why this function only ever adds
    scope to the guard and is never, on its own, the reason a hit is cleared.
    """
    count = 0
    for line in lines[:index]:
        position = 0
        while (found := line.find("`", position)) != -1:
            if found == 0 or line[found - 1] != "\\":
                count += 1
            position = found + 1
    return count % 2 == 1


def _dangerous_opener_lines(text: str) -> list[int]:
    """Return the 1-based line numbers of every dangerous, backtick-embedded opener in one file."""
    lines = text.splitlines()
    return [
        index + 1
        for index in range(len(lines))
        if _would_open_a_multiline_comment_region(lines, index) and _is_inside_an_open_backtick_region(lines, index)
    ]


def _iter_js_like_files(root: Path) -> Iterator[Path]:
    """Yield every TS/JS-family file under a `SCAN_SURFACES` root, narrowest surface first.

    Mirrors `readers.py::_iter_surface_files`'s own claim-once walk (same excluded directories, same
    narrowest-first order) closely enough that a file is never visited twice, without depending on
    that private helper: this guard only needs the union of files, not which surface owns each one.
    """
    claimed: set[Path] = set()
    for surface in SCAN_SURFACES:
        if not (surface.suffixes & _JS_LIKE_SUFFIXES):
            continue
        surface_root = root / surface.root
        if surface_root.is_file():
            candidates = [surface_root]
        elif surface_root.is_dir():
            candidates = sorted(surface_root.rglob("*"))
        else:
            candidates = []
        for path in candidates:
            if not path.is_file() or path.suffix not in _JS_LIKE_SUFFIXES:
                continue
            if any(part in EXCLUDED_DIRECTORY_NAMES for part in path.parts):
                continue
            resolved = path.resolve()
            if resolved in claimed:
                continue
            claimed.add(resolved)
            yield path


def test_predicate_fires_on_the_reviewers_exact_repro() -> None:
    """The literal reproduction from the independent review that found this gap."""
    text = "const sql = `\n/* fake sql comment inside a template literal\nSELECT * FROM geo.mv_reader_probe;\n*/\n`;\n"

    assert _dangerous_opener_lines(text) == [2]


def test_predicate_does_not_fire_on_jsdoc() -> None:
    """The ubiquitous, safe shape this guard must never flag -- `/**` is excluded on purpose."""
    text = "const sql = `\n/**\n * Explains something.\n */\nSELECT 1;\n`;\n"

    assert _dangerous_opener_lines(text) == []


def test_predicate_does_not_fire_on_a_same_line_jsx_comment() -> None:
    """`{/* ... */}` neither owns its line nor spans past it -- never a candidate."""
    text = "return (\n  <div>{/* explains the layout */}</div>\n);\n"

    assert _dangerous_opener_lines(text) == []


def test_predicate_does_not_fire_on_an_opener_with_no_closer_anywhere() -> None:
    """No `*/` anywhere below means `_code_only_lines` itself keeps the line as code -- a false
    block, never a false clear -- so this predicate must not manufacture a hit for it either, even
    inside an open template literal.
    """
    text = "const sql = `\n/* an unterminated comment naming geo.mv_reader_probe\nSELECT 1;\n`;\n"

    assert _dangerous_opener_lines(text) == []


def test_predicate_does_not_fire_on_a_genuine_comment_outside_any_template_literal() -> None:
    """Backtick-context narrowing's whole point: a real explanatory block comment with no backtick
    anywhere near it cannot possibly be hiding inside a template literal, so it must not need an
    allowlist entry to stay quiet. This is the common, idiomatic shape across this codebase (a
    top-level `/*\n * ...\n */` block), and it is what made a backtick-blind version of this guard
    un-shippable: it fired on 18 of these on the very first real-tree run.
    """
    text = "/*\n * A genuine, harmless multi-line comment nowhere near a template literal.\n */\n"

    assert _dangerous_opener_lines(text) == []


def test_predicate_does_not_fire_once_an_earlier_template_literal_has_closed() -> None:
    """Backtick parity is cumulative across the whole file: a template literal that opened and
    closed earlier leaves parity even again, so a later, unrelated comment is correctly read as
    outside any template literal -- the shape validated against `environmental-read-model.ts`, which
    closes hundreds of real SQL-building backticks long before its own doc comments.
    """
    text = "const q = `SELECT 1`;\n\n/*\n * Explains something, well outside the query above.\n */\n"

    assert _dangerous_opener_lines(text) == []


def test_predicate_still_fires_on_a_harmless_comment_that_happens_to_sit_inside_a_template_literal() -> None:
    """The residual false-block cost backtick narrowing does not remove: CSS and JS block comments
    share the identical `/* */` delimiter, so a real, harmless CSS comment inside a template literal
    that builds a plain string (never a query) is indistinguishable from the dangerous case by shape
    alone. This is exactly `LayerTimeSlider.tsx`'s real shape, which is why
    `_ACKNOWLEDGED_TEMPLATE_LITERAL_COMMENTS` exists rather than a cleverer heuristic here.
    """
    text = (
        "const styles = `\n"
        "  .foo {\n"
        "    /* just a CSS comment, nothing to do with any relation\n"
        "    color: red; */\n"
        "  }\n"
        "`;\n"
    )

    assert _dangerous_opener_lines(text) == [3]


@dataclass(frozen=True, slots=True)
class _AcknowledgedTemplateLiteralComment:
    """One exact `(path, line)` this guard's own review confirmed is a REAL comment embedded in a
    plain (untagged) template literal -- CSS shares the identical `/* */` delimiter with JS block
    comments, so backtick-context narrowing alone cannot tell the two apart, and sniffing the
    span's own content for a relation-shaped identifier was rejected as its own unreliable
    heuristic (see the module note above). Pinned by exact line, not by path alone, so ANY edit to
    the file -- content changed, a line added above it -- makes this entry stale
    (`unmatched_acknowledgements` below) rather than silently continuing to bless a line that has
    moved. The same "asserted, never absent" contract `readers.py::ReaderExemption` already keeps
    for reader hits.
    """

    path: str
    line: int
    reason: str


#: Reviewed by hand when this guard was introduced (2026-09-05): all four are CSS comments inside
#: `layerTimeSliderStyles`, a PLAIN backtick string of raw CSS text (never a query), and none of the
#: four spans contains anything shaped like a relation reference. A fifth entry anywhere outside
#: this one file is a reason to stop and re-read this module's note before adding it, not a reason
#: to assume the pattern is now routine.
_ACKNOWLEDGED_TEMPLATE_LITERAL_COMMENTS: Final[tuple[_AcknowledgedTemplateLiteralComment, ...]] = (
    _AcknowledgedTemplateLiteralComment(
        path="src/components/map/layer-panel/LayerTimeSlider.tsx",
        line=58,
        reason="CSS comment ('Taller than the 12px track...'); no relation-shaped text in it",
    ),
    _AcknowledgedTemplateLiteralComment(
        path="src/components/map/layer-panel/LayerTimeSlider.tsx",
        line=102,
        reason="CSS comment ('Pending affordance...'); no relation-shaped text in it",
    ),
    _AcknowledgedTemplateLiteralComment(
        path="src/components/map/layer-panel/LayerTimeSlider.tsx",
        line=137,
        reason="CSS comment ('Without this the UA paints...'); no relation-shaped text in it",
    ),
    _AcknowledgedTemplateLiteralComment(
        path="src/components/map/layer-panel/LayerTimeSlider.tsx",
        line=143,
        reason="CSS comment ('44px tap target...'); no relation-shaped text in it",
    ),
)


def test_no_unacknowledged_scanned_js_surface_carries_a_line_initial_single_star_block_opener() -> None:
    """The tripwire itself, run against the real checkout rather than a synthetic one.

    `retirement/AGENTS.md` ("The comment stripper carries state across lines, and still refuses to
    guess") documents that a `/*` owning its line inside a multi-line backtick template literal
    would be misread as a comment. This is the thing that watches for it: it walks the same TS/JS
    surfaces `SCAN_SURFACES` scans, and any UNACKNOWLEDGED instance -- one not already read by hand
    and pinned in `_ACKNOWLEDGED_TEMPLATE_LITERAL_COMMENTS` above -- fails the build. So the day this
    shape appears anywhere new, THIS test is what notices, not a packet that silently reports a
    relation as having zero readers.

    The second assertion is the mirror of the first: an acknowledgement whose exact line no longer
    matches the dangerous shape is STALE -- the file moved, or the comment is gone -- and carrying it
    forward would be the same silent-drift risk `readers.py::ReaderScan.unused_exemptions` exists to
    catch for reader hits.
    """
    root = find_repository_root()
    found: dict[tuple[str, str], int] = {}
    for path in _iter_js_like_files(root):
        relative = path.relative_to(root).as_posix()
        for line in _dangerous_opener_lines(path.read_text(encoding="utf-8", errors="replace")):
            found[(relative, line)] = line

    acknowledged = {(entry.path, entry.line) for entry in _ACKNOWLEDGED_TEMPLATE_LITERAL_COMMENTS}
    unacknowledged = sorted(f"{path}:{line}" for path, line in found if (path, line) not in acknowledged)
    stale = sorted(
        f"{entry.path}:{entry.line}"
        for entry in _ACKNOWLEDGED_TEMPLATE_LITERAL_COMMENTS
        if (entry.path, entry.line) not in found
    )

    assert unacknowledged == [], (
        "line-initial `/*` (not `/**`), genuinely inside an open backtick template literal, on a "
        f"TS/JS surface `readers.py` scans, with no entry in _ACKNOWLEDGED_TEMPLATE_LITERAL_COMMENTS: "
        f"{unacknowledged}. This is the one documented path to a FALSE CLEAR "
        "(services/agri-data-service/src/agri_data_service/retirement/AGENTS.md, 'The comment "
        "stripper carries state across lines, and still refuses to guess'): `_appears_after` proves "
        "only that a `*/` exists somewhere below this opener, never that it belongs to the same "
        "string, so `_code_only_lines` will read everything up to the next `*/` in the file as a "
        "comment -- including a genuine `SELECT ... FROM <relation>` -- and the drop-packet scan "
        "will report a relation something still reads as having zero readers. Read the cited "
        "line(s) first: if the template literal this opener sits in builds SQL (or any string a "
        "relation name could appear in) for a relation that is still being retired, treat this as a "
        "live false-clear risk before touching this test. Only once you have confirmed the span "
        "between this opener and its closer contains no relation reference should you either rewrite "
        "the comment (as `/**` or `//` lines) or add a new, individually-reasoned entry to "
        "_ACKNOWLEDGED_TEMPLATE_LITERAL_COMMENTS."
    )
    assert stale == [], (
        f"_ACKNOWLEDGED_TEMPLATE_LITERAL_COMMENTS entries that no longer match a real dangerous "
        f"opener at that exact line (the file moved, or the comment changed): {stale}. Re-verify the "
        "file at that path: if the comment is simply gone, delete the entry; if it moved to a new "
        "line, update the line number after re-reading it in place."
    )
