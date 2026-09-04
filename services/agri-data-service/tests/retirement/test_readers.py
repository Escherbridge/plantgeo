"""The zero-reader proof: what it counts as a consumer, what it exempts, and what it refuses to claim."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.retirement.readers import (
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
    from collections.abc import Sequence
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


def test_a_block_comment_left_open_on_its_line_is_not_stripped(tmp_path: Path) -> None:
    """A blind spot the docstring names: a comment spanning lines looks, from one line, unclosed.

    The heuristic keeps the rest of the line as code rather than guess where such a comment ends, so
    a reference on the opening line still blocks -- a false block, never a false clear.
    """
    root = build_checkout(tmp_path, files={"src/a.ts": "/* start of a long comment about geo.mv_reader_probe\n"})

    scan = _scan(root)

    assert scan.zero_readers is False
    assert scan.consumers[0].disposition is ReaderDisposition.CONSUMER


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
