"""source_id generation, bounded fetching, add-source idempotence, import-chunked merge/reject, bootstrap."""

import copy
import itertools
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import FIXTURES, SOURCE_ID, SOURCE_URL, copy_fixture_raw, copy_fixture_registry, seed_store

from strategy_knowledge import cli
from strategy_knowledge.corpus import SOURCES_FILE, CorpusStore, is_valid_source_id, read_json, sha256_file, write_json
from strategy_knowledge.fetch import (
    FetchedDocument,
    FetchError,
    build_source_id,
    disambiguate,
    fetch_document,
    parse_header,
    raw_body,
    raw_file_content,
    url_host,
    url_slug,
)
from strategy_knowledge.ingest import (
    CHUNK_BRIEF_PATH,
    LINES_PER_CHUNKING_AGENT,
    SLICE_ASSIGNMENTS_FILE,
    BlockScope,
    CandidateConflictError,
    ImportOptions,
    RawFileMissingError,
    add_source,
    bootstrap,
    import_chunked,
    merge_candidates,
    merge_plan,
    slice_assignments,
)
from strategy_knowledge.models import CandidateStrategy, RegistryStrategy

NEW_URL = "https://www.nrcs.usda.gov/sites/default/files/cover-crop-guide.pdf"
ORIGINAL_TEXT = "Cover crops protect soil.\nPlant rye after harvest."
CHANGED_TEXT = "Cover crops protect soil.\nPlant rye right after harvest."
COVER_CANDIDATE = "winter-cereal-rye-cover"


def test_source_id_is_date_host_slug() -> None:
    assert build_source_id(NEW_URL, datetime(2026, 10, 3, tzinfo=UTC)) == "20261003-nrcs-cover-crop-guide"
    assert url_host("https://extension.usu.edu/ecorestore/x") == "extension"


def test_slug_skips_numeric_segments_and_falls_back_to_the_title() -> None:
    assert url_slug("https://example.org/DocumentCenter/View/1274") == "view"
    assert url_slug("https://example.org/", "Soil Health: A Primer!") == "soil-health-a-primer"
    assert url_slug("https://example.org/") == "page"
    long_slug = url_slug("https://example.org/" + "-".join(["segment"] * 20))
    assert len(long_slug) <= 60
    assert not long_slug.endswith("-")


def test_disambiguation_appends_a_url_digest_not_a_sequence_number() -> None:
    first = disambiguate("20261003-nrcs-guide", "https://a.example/guide")
    second = disambiguate("20261003-nrcs-guide", "https://b.example/guide")
    assert first.startswith("20261003-nrcs-guide-")
    assert len(first) == len("20261003-nrcs-guide-") + 6
    assert first != second


def _document(text: str) -> FetchedDocument:
    return FetchedDocument(
        url=NEW_URL,
        final_url=NEW_URL,
        kind="pdf",
        title="Cover Crop Guide",
        text=text,
        fetched_at=datetime.now(UTC).isoformat(),
    )


def test_raw_file_header_round_trips() -> None:
    content = raw_file_content(_document("First line.\nSecond line.\n\n"))
    assert parse_header(content)["source_url"] == NEW_URL
    assert parse_header(content)["kind"] == "pdf"
    assert raw_body(content) == "First line.\nSecond line."
    assert not content.endswith("\n")


def test_add_source_registers_then_is_a_no_op_then_detects_change(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "cache")
    responses = [ORIGINAL_TEXT, ORIGINAL_TEXT, CHANGED_TEXT]
    moment = datetime(2026, 10, 3, tzinfo=UTC)

    def fetcher(_url: str) -> FetchedDocument:
        return _document(responses.pop(0))

    added = add_source(store, NEW_URL, fetcher=fetcher, now=moment)
    assert (added.source_id, added.status) == ("20261003-nrcs-cover-crop-guide", "added")
    raw_path = store.raw_path(added.source_id)
    first_hash = sha256_file(raw_path)
    assert store.load_sources()[added.source_id].sha256 == first_hash
    assert added.line_count == 8
    assert (store.work_path(added.source_id) / "strategy_ids.txt").is_file()
    assert any(str(CHUNK_BRIEF_PATH) in step for step in added.next_steps)
    work = store.work_path(added.source_id)
    assert slice_assignments(work / SLICE_ASSIGNMENTS_FILE, "S1") == {added.source_id: [[1, 8]]}
    assert any(f"import-chunked {work / 'S1.json'} --assignments" in step for step in added.next_steps)

    unchanged = add_source(store, NEW_URL, fetcher=fetcher, now=moment)
    assert unchanged.status == "unchanged"
    assert sha256_file(raw_path) == first_hash

    changed = add_source(store, NEW_URL, fetcher=fetcher, now=moment)
    assert (changed.source_id, changed.status) == (added.source_id, "changed")
    assert store.load_sources()[added.source_id].sha256 != first_hash


def test_import_rejects_a_block_with_a_coverage_gap(tmp_path: Path, fixture_document: dict[str, Any]) -> None:
    store = seed_store(tmp_path / "second")
    broken = copy.deepcopy(fixture_document)
    del broken["sources"][0]["chunks"][2]
    outcome = import_chunked(store, broken, "T1")
    assert outcome.imported == {}
    assert any("uncovered lines" in problem for problem in outcome.rejected[SOURCE_ID])


def test_candidates_from_two_slices_of_one_source_are_all_kept(fixture_document: dict[str, Any]) -> None:
    block = fixture_document["sources"][0]
    first = merge_plan(None, BlockScope(SOURCE_ID, "sha", "U1", [(1, 20)]), block)
    both = merge_plan(first, BlockScope(SOURCE_ID, "sha", "U2", [(21, 33)]), block)
    keys = sorted((candidate.origin_slice, candidate.strategy_id) for candidate in both.candidate_strategies)
    assert keys == [("U1", COVER_CANDIDATE), ("U2", COVER_CANDIDATE)]
    reimported = merge_plan(both, BlockScope(SOURCE_ID, "sha", "U1", [(1, 20)]), block)
    assert len(reimported.candidate_strategies) == 2


def test_repeated_candidate_within_one_slice_folds_citations(fixture_document: dict[str, Any]) -> None:
    row = fixture_document["sources"][0]["candidate_strategies"][0]
    extra_citation = {"source_id": SOURCE_ID, "locator": "p2", "excerpt": "a different verbatim span of the fixture"}
    repeat = {**row, "sources": [extra_citation], "application_rate": "2 tons/acre"}
    block = {**fixture_document["sources"][0], "candidate_strategies": [row, repeat]}
    plan = merge_plan(None, BlockScope(SOURCE_ID, "sha", "U1", [(1, 33)], whole_file=True), block)
    (folded,) = plan.candidate_strategies
    assert len(folded.sources) == len(row["sources"]) + 1
    assert folded.application_rate.endswith("2 tons/acre")


def test_repeated_candidates_fold_to_the_strongest_claims(fixture_document: dict[str, Any]) -> None:
    """The real shape: the 2nd and 3rd copies carry stated goals, stronger evidence, more actions, a timing."""
    row = copy.deepcopy(fixture_document["sources"][0]["candidate_strategies"][0])
    extra_citation = {"source_id": SOURCE_ID, "locator": "p2", "excerpt": "a different verbatim span of the fixture"}
    first = {
        **row,
        "goals": {"erosion_control": "inferred"},
        "evidence_strength": "expert_guidance",
        "actions": ["Drill rye after harvest"],
        "timing": None,
        "notes": "from the cover crop section",
    }
    second = {
        **row,
        "goals": {"erosion_control": "stated", "soil_health": "stated"},
        "evidence_strength": "peer_reviewed_experiment",
        "actions": ["Drill rye after harvest", "Roll-crimp before planting"],
        "timing": "late summer or fall",
        "notes": "from the trial table",
        "sources": [extra_citation],
    }
    third = {
        **row,
        "goals": {"soil_health": "inferred", "water_management": "stated"},
        "evidence_strength": "field_trial_or_case_study",
        "actions": ["Terminate before the cash crop"],
        "timing": "spring",
        "notes": "from the cover crop section",
    }
    block = {"candidate_strategies": [first, second, third]}
    plan = merge_plan(None, BlockScope(SOURCE_ID, "sha", "U1", [(1, 33)], whole_file=True), block)
    (folded,) = plan.candidate_strategies
    assert folded.goals == {"erosion_control": "stated", "soil_health": "stated", "water_management": "stated"}
    assert folded.evidence_strength == "peer_reviewed_experiment"
    assert folded.actions == ["Drill rye after harvest", "Roll-crimp before planting", "Terminate before the cash crop"]
    assert folded.timing == "late summer or fall"
    assert folded.notes == "from the cover crop section | from the trial table"
    assert folded.matches_existing == "grass-cover-cropping"
    assert folded.origin_slice == "U1"
    assert len(folded.sources) == 2


def test_conflicting_matches_in_one_slice_are_rejected_not_picked(
    fixture_store: CorpusStore,
    fixture_document: dict[str, Any],
) -> None:
    document = copy.deepcopy(fixture_document)
    row = document["sources"][0]["candidate_strategies"][0]
    conflicting = {**row, "matches_existing": "post-fire-straw-mulching"}
    document["sources"][0]["candidate_strategies"] = [row, conflicting]
    before = fixture_store.plan_path(SOURCE_ID).read_bytes()
    for accept_problems in (False, True):
        outcome = import_chunked(fixture_store, document, "T1", ImportOptions(accept_problems=accept_problems))
        assert any("conflicting matches_existing" in problem for problem in outcome.rejected[SOURCE_ID])
    assert fixture_store.plan_path(SOURCE_ID).read_bytes() == before
    with pytest.raises(CandidateConflictError, match="conflicting matches_existing"):
        merge_candidates(CandidateStrategy.model_validate(row), CandidateStrategy.model_validate(conflicting))


def test_registry_strategy_treats_null_facets_as_none_authored() -> None:
    row = {
        "strategy_id": "post-fire-tree-survival-assessment",
        "name": "Tree survival",
        "category": "monitoring_assessment",
        "evidence_strength": "expert_guidance",
    }
    assert RegistryStrategy.model_validate({**row, "facets": None}).facets.overview is None


def test_merge_candidate_may_omit_name_but_a_new_candidate_may_not(fixture_document: dict[str, Any]) -> None:
    row = {**fixture_document["sources"][0]["candidate_strategies"][0], "name": None, "summary": None}
    assert CandidateStrategy.model_validate(row).matches_existing == "grass-cover-cropping"
    with pytest.raises(ValueError, match="needs a name and a summary"):
        CandidateStrategy.model_validate({**row, "matches_existing": None})


def test_reimport_replaces_only_the_assigned_range(
    fixture_store: CorpusStore,
    fixture_document: dict[str, Any],
) -> None:
    block = copy.deepcopy(fixture_document["sources"][0])
    cover_chunk = next(chunk for chunk in block["chunks"] if chunk["line_start"] == 27)
    references = next(chunk for chunk in block["chunks"] if chunk["line_start"] == 32)
    first_half = {**cover_chunk, "chunk_id": f"{SOURCE_ID}#L27-29", "line_end": 29, "linked_finding_ids": []}
    second_half = {**cover_chunk, "chunk_id": f"{SOURCE_ID}#L30-31", "line_start": 30}
    partial = {
        "slice_id": "T2",
        "sources": [
            {
                "source_id": SOURCE_ID,
                "chunks": [first_half, second_half, references],
                "skipped": [],
                "findings": [finding for finding in block["findings"] if finding["line_start"] >= 27],
                "candidate_strategies": [],
            },
        ],
    }
    outcome = import_chunked(fixture_store, partial, "T2", ImportOptions(assignments={SOURCE_ID: [[27, 33]]}))
    assert outcome.rejected == {}
    plan = fixture_store.load_plan(SOURCE_ID)
    assert plan is not None
    ranges = [chunk.chunk_id.split("#")[1] for chunk in plan.chunks]
    assert ranges == ["L7-13", "L14-20", "L21-26", "L27-29", "L30-31", "L32-33"]
    assert plan.assigned_ranges == [(1, 33)]
    assert len(plan.candidate_strategies) == 1
    findings = fixture_store.load_findings(SOURCE_ID)
    assert findings is not None
    assert len(findings.findings) == 3


def test_host_labels_are_capped_so_generated_ids_stay_valid() -> None:
    url = f"https://{'a' * 63}.example.org/{'-'.join(['segment'] * 20)}.pdf"
    source_id = disambiguate(build_source_id(url, datetime(2026, 10, 3, tzinfo=UTC)), url)
    assert is_valid_source_id(source_id)


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_body_over_the_byte_ceiling_is_refused_while_streaming() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>" + b"x" * 5000 + b"</p>")

    with pytest.raises(FetchError, match="ceiling"):
        fetch_document("https://example.org/big", _client(handler), maximum_bytes=1000)


def test_the_wall_clock_deadline_stops_a_download() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>slow body</p>")

    with pytest.raises(FetchError, match="did not finish"):
        fetch_document("https://example.org/slow", _client(handler), total_timeout_seconds=0)


def test_transport_and_pdf_failures_become_fetch_errors() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(FetchError, match="ConnectError"):
        fetch_document("https://example.org/down", _client(refused))

    def broken_pdf(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7 not really a pdf")

    with pytest.raises(FetchError, match=r"broken\.pdf"):
        fetch_document("https://example.org/broken.pdf", _client(broken_pdf))

    def missing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(FetchError, match="HTTP 404"):
        fetch_document("https://example.org/missing", _client(missing))


def test_add_source_refuses_a_registered_url_whose_raw_file_is_not_local(fixture_store: CorpusStore) -> None:
    fixture_store.raw_path(SOURCE_ID).unlink()
    before = fixture_store.path(SOURCES_FILE).read_bytes()

    def fetcher(_url: str) -> FetchedDocument:
        raise AssertionError("must not fetch before refusing")

    with pytest.raises(RawFileMissingError, match="sync pull"):
        add_source(fixture_store, SOURCE_URL, fetcher=fetcher)
    assert not fixture_store.raw_path(SOURCE_ID).exists()
    assert fixture_store.path(SOURCES_FILE).read_bytes() == before


def test_a_rejected_import_does_not_touch_sources_json(tmp_path: Path, fixture_document: dict[str, Any]) -> None:
    store = CorpusStore(tmp_path / "fresh")
    copy_fixture_raw(store)
    broken = copy.deepcopy(fixture_document)
    del broken["sources"][0]["chunks"][2]
    outcome = import_chunked(store, broken, "T1")
    assert SOURCE_ID in outcome.rejected
    assert not store.path(SOURCES_FILE).exists()
    assert store.load_plan(SOURCE_ID) is None


def test_assignments_are_checked_both_ways(fixture_store: CorpusStore, fixture_document: dict[str, Any]) -> None:
    missing = import_chunked(fixture_store, {"sources": []}, "T3", ImportOptions(assignments={SOURCE_ID: [[1, 33]]}))
    assert missing.rejected == {SOURCE_ID: [f"assigned source missing from output: {SOURCE_ID}"]}
    other_source = ImportOptions(assignments={"some-other-source": [[1, 10]]})
    unassigned = import_chunked(fixture_store, fixture_document, "T1", other_source)
    assert unassigned.rejected[SOURCE_ID] == [f"{SOURCE_ID}: not assigned to this slice"]
    assert unassigned.rejected["some-other-source"] == ["assigned source missing from output: some-other-source"]


def test_bootstrap_imports_any_slice_id_and_reports_slices_without_output(tmp_path: Path) -> None:
    research = tmp_path / "research"
    (research / "raw").mkdir(parents=True)
    (research / "raw" / f"{SOURCE_ID}.txt").write_bytes((FIXTURES / "raw" / f"{SOURCE_ID}.txt").read_bytes())
    document = read_json(FIXTURES / "chunked_fixture.json")
    document["slice_id"] = "Z9"
    write_json(research / "chunked" / "Z9.json", document)
    write_json(research / "chunked" / "Q1.json", {"slice_id": "Q1", "sources": []})
    registry = read_json(FIXTURES / "strategy_registry.json")["strategies"]
    known = [f"{row['strategy_id']} | {row['name']} | {row['category']}" for row in registry]
    (research / "briefs").mkdir()
    (research / "briefs" / "strategy_ids.txt").write_text("".join(f"{line}\n" for line in known), encoding="utf-8")
    write_json(
        research / "briefs" / "slice_assignments.json",
        {
            "Z9": [{"source_id": SOURCE_ID, "ranges": [[1, 33]]}],
            "D1": [{"source_id": SOURCE_ID, "ranges": [[34, 40]]}],
        },
    )
    store = CorpusStore(tmp_path / "cache")
    summary = bootstrap(store, research)
    assert summary["slices"]["Z9"]["rejected"] == {}
    assert summary["slices"]["Z9"]["imported"][SOURCE_ID]["chunks"] == 5
    assignments_path = research / "briefs" / "slice_assignments.json"
    assert summary["slices"]["Q1"]["rejected"] == {"*": [f"slice Q1 is not in {assignments_path}"]}
    assert summary["slices_without_output"] == ["D1"]


def test_import_needs_the_resolved_slice_id(fixture_store: CorpusStore, fixture_document: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="resolved slice id"):
        import_chunked(fixture_store, fixture_document, "")


def _half_blocks(block: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The fixture block split at line 20/21; each half keeps the candidate strategy."""
    first = copy.deepcopy(block)
    first["chunks"] = [chunk for chunk in block["chunks"] if chunk["line_end"] <= 20]
    first["findings"] = [finding for finding in block["findings"] if finding["line_start"] <= 20]
    second = copy.deepcopy(block)
    second["chunks"] = [chunk for chunk in block["chunks"] if chunk["line_start"] >= 21]
    second["findings"] = [finding for finding in block["findings"] if finding["line_start"] >= 21]
    second["skipped"] = []
    return first, second


def test_two_slice_less_documents_for_one_source_keep_both_candidates(
    tmp_path: Path,
    fixture_document: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither document names a slice: each is keyed by its file stem, as the CLI resolves it, never by None."""
    store = CorpusStore(tmp_path / "halves")
    copy_fixture_raw(store)
    copy_fixture_registry(store)
    first, second = _half_blocks(fixture_document["sources"][0])
    work = tmp_path / "work"
    write_json(work / "first-half.json", {"sources": [first]})
    write_json(work / "second-half.json", {"sources": [second]})
    write_json(
        work / "slice_assignments.json",
        {
            "first-half": [{"source_id": SOURCE_ID, "ranges": [[1, 20]]}],
            "second-half": [{"source_id": SOURCE_ID, "ranges": [[21, 33]]}],
        },
    )
    monkeypatch.setenv("STRATEGY_KB_CACHE_DIR", str(store.root))
    assignments = str(work / "slice_assignments.json")
    for name in ("first-half", "second-half"):
        assert cli.main(["import-chunked", str(work / f"{name}.json"), "--assignments", assignments]) == cli.EXIT_OK
    plan = store.load_plan(SOURCE_ID)
    assert plan is not None
    keys = sorted((candidate.origin_slice, candidate.strategy_id) for candidate in plan.candidate_strategies)
    assert keys == [("first-half", COVER_CANDIDATE), ("second-half", COVER_CANDIDATE)]


def test_a_whole_file_reimport_replaces_every_stored_candidate(
    fixture_store: CorpusStore,
    fixture_document: dict[str, Any],
) -> None:
    """A re-chunk under a new slice id owns the whole file, so the old slice's candidates must not linger."""
    document = copy.deepcopy(fixture_document)
    document["sources"][0]["candidate_strategies"][0]["strategy_id"] = "winter-rye-cover-rechunked"
    assert import_chunked(fixture_store, document, "R1").rejected == {}
    plan = fixture_store.load_plan(SOURCE_ID)
    assert plan is not None
    keys = [(candidate.origin_slice, candidate.strategy_id) for candidate in plan.candidate_strategies]
    assert keys == [("R1", "winter-rye-cover-rechunked")]
    assert import_chunked(fixture_store, document, "R1").rejected == {}
    again = fixture_store.load_plan(SOURCE_ID)
    assert again is not None
    assert len(again.candidate_strategies) == 1


def test_add_source_splits_a_long_source_into_agent_slices_at_blank_lines(tmp_path: Path) -> None:
    paragraphs = ["\n".join(f"Paragraph {paragraph} line {line}." for line in range(9)) for paragraph in range(400)]
    store = CorpusStore(tmp_path / "cache")

    def fetcher(_url: str) -> FetchedDocument:
        return _document("\n\n".join(paragraphs))

    outcome = add_source(store, NEW_URL, fetcher=fetcher, now=datetime(2026, 10, 3, tzinfo=UTC))
    work = store.work_path(outcome.source_id)
    assignments = read_json(work / SLICE_ASSIGNMENTS_FILE)
    assert list(assignments) == ["S1", "S2", "S3"]
    ranges = [rows[0]["ranges"][0] for rows in assignments.values()]
    assert ranges[0][0] == 1
    assert ranges[-1][1] == outcome.line_count
    assert all(later[0] == earlier[1] + 1 for earlier, later in itertools.pairwise(ranges))
    assert all(end - start + 1 <= LINES_PER_CHUNKING_AGENT for start, end in ranges)
    raw_lines = store.raw_lines(outcome.source_id)
    assert all(raw_lines[end - 1] == "" for _start, end in ranges[:-1])
    assert slice_assignments(work / SLICE_ASSIGNMENTS_FILE, "S2") == {outcome.source_id: [ranges[1]]}
    imports = [step for step in outcome.next_steps if "import-chunked" in step]
    assert len(imports) == 3
    assert all("--assignments" in step for step in imports)


def test_legacy_candidates_without_a_slice_still_load(fixture_store: CorpusStore) -> None:
    plan_path = fixture_store.plan_path(SOURCE_ID)
    payload = read_json(plan_path)
    for row in payload["candidate_strategies"]:
        row["origin_slice"] = None
    write_json(plan_path, payload)
    legacy = fixture_store.load_plan(SOURCE_ID)
    assert legacy is not None
    assert [candidate.origin_slice for candidate in legacy.candidate_strategies] == [None]
    scope = BlockScope(SOURCE_ID, legacy.raw_sha256 or "", "P9", [(27, 33)])
    kept = merge_plan(legacy, scope, {"candidate_strategies": []})
    assert [candidate.origin_slice for candidate in kept.candidate_strategies] == [None]
