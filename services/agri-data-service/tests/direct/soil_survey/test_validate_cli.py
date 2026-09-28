"""`validate`: reads local capture storage before staging, bounded area groups, and SDA faults.

USDA SDA is always an `httpx.MockTransport`; the bucket adapter is patched to fail if built.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final, NoReturn

import httpx
import pytest

from agri_data_service.foundation.soil_survey.receipts import SoilSurveyError
from agri_data_service.pipeline.direct.soil_survey import __main__ as soil_survey_main
from agri_data_service.pipeline.direct.soil_survey.local_storage import LocalCandidateStorage
from agri_data_service.pipeline.direct.soil_survey.prepare import prepare_candidate
from agri_data_service.pipeline.validation import soil_survey as validation_module
from agri_data_service.pipeline.validation.soil_survey import (
    MAX_VALIDATION_AREAS,
    MAX_VALIDATION_PART_BYTES,
    HttpxSoilSurveySdaClient,
    SoilSurveyValidationError,
    group_areas_for_validation,
    validate_soil_survey_candidate,
)
from tests.direct.soil_survey.fakes import AREA, POLYGONS, completed

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.foundation.soil_survey.release import Candidate

SHARD: Final = "ID-1"
#: SDA's own US-locale `saverest` text for the fakes' 2025-08-27 vintage.
SDA_SAVEREST: Final = "8/27/2025 8:27:08 PM"
MEBIBYTE: Final = 1024 * 1024
SMALL_AREA_BYTES: Final = 2 * MEBIBYTE
LARGE_AREA_BYTES: Final = 40 * MEBIBYTE
MANY_AREAS: Final = 120
LARGE_AREAS: Final = 7
EXPECTED_ROWS: Final = len(POLYGONS)


async def _prepared(root: Path) -> tuple[Candidate, str]:
    await completed(root)
    return prepare_candidate(root, SHARD, [AREA])


def _sda(*, count: int = EXPECTED_ROWS, status: int = httpx.codes.OK) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["format"] == "JSON+COLUMNNAME"
        if status != httpx.codes.OK:
            return httpx.Response(status)
        return httpx.Response(status, json={"Table": [["delineation_count", "saverest"], [str(count), SDA_SAVEREST]]})

    return httpx.MockTransport(handler)


def _patch_transport(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Route every `httpx.AsyncClient()` the CLI builds inline to `transport`."""
    original_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("transport", transport)
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _refuse(*arguments: object, **keywords: object) -> NoReturn:
    raise AssertionError(f"the bucket must not be touched: {arguments} {keywords}")


async def test_validate_reads_local_capture_storage_and_never_the_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, sha = await _prepared(tmp_path)
    monkeypatch.setattr(soil_survey_main.BotoAvailabilityStorage, "from_settings", _refuse)
    _patch_transport(monkeypatch, _sda())
    arguments = soil_survey_main.parser().parse_args(
        ["validate", "--root", str(tmp_path), "--manifest", sha, "--apply"]
    )
    report = await soil_survey_main._validate(arguments)
    assert report["outcome"] == "validated"
    assert report["storage"] == "local"
    assert report["groups"] == [[AREA]]
    assert report["findings"] == []


async def test_validate_dry_run_prints_groups_without_a_network_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, sha = await _prepared(tmp_path)
    soil_survey_main.main(["validate", "--root", str(tmp_path), "--manifest", sha])
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "dry_run"
    assert report["groups"] == [[AREA]]
    assert report["network_calls"] == 1


def test_area_groups_hold_fifty_areas_and_128_mib() -> None:
    small = {f"ID{index:03d}": SMALL_AREA_BYTES for index in range(MANY_AREAS)}
    groups = group_areas_for_validation(small)
    assert [area for group in groups for area in group] == sorted(small)
    assert all(len(group) <= MAX_VALIDATION_AREAS for group in groups)
    assert len(groups[0]) == MAX_VALIDATION_AREAS
    large = {f"ID{index:03d}": LARGE_AREA_BYTES for index in range(LARGE_AREAS)}
    large_groups = group_areas_for_validation(large)
    assert all(sum(large[area] for area in group) <= MAX_VALIDATION_PART_BYTES for group in large_groups)
    assert [len(group) for group in large_groups] == [3, 3, 1]
    with pytest.raises(SoilSurveyValidationError, match="alone"):
        group_areas_for_validation({AREA: MAX_VALIDATION_PART_BYTES + 1})


async def test_source_failure_and_count_mismatch_become_findings(tmp_path: Path) -> None:
    candidate, _ = await _prepared(tmp_path)
    storage = LocalCandidateStorage(tmp_path)
    async with httpx.AsyncClient(transport=_sda(status=httpx.codes.SERVICE_UNAVAILABLE)) as client:
        failed = await validate_soil_survey_candidate(
            candidate, storage, HttpxSoilSurveySdaClient(client=client), survey_area_symbols=[AREA]
        )
    assert [finding.kind for finding in failed.findings] == ["source_query_failed"]
    async with httpx.AsyncClient(transport=_sda(count=len(POLYGONS) + 1)) as client:
        mismatch = await validate_soil_survey_candidate(
            candidate, storage, HttpxSoilSurveySdaClient(client=client), survey_area_symbols=[AREA]
        )
    assert [finding.kind for finding in mismatch.findings] == ["delineation_count_mismatch"]


async def test_budget_exhaustion_refuses_unreached_areas_without_losing_earlier_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 3: a whole-group timeout must not cancel the area in flight and lose every result."""
    monkeypatch.setattr(validation_module, "_written_candidate_areas", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(validation_module, "_CANDIDATE_VALIDATION_SECONDS", 0.05)

    class _SlowClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def fetch_survey_area_summary(self, area: str) -> object:
            self.calls.append(area)
            await asyncio.sleep(0.2)  # longer than the whole (patched) budget
            raise AssertionError("must not resolve past the budget")

    client = _SlowClient()
    candidate = SimpleNamespace(release_day=date(2025, 8, 27))
    report = await validate_soil_survey_candidate(
        candidate, storage=None, sda_client=client, survey_area_symbols=["ID001", "ID002", "ID003"]
    )
    # Only the area in flight when the budget ran out was ever attempted...
    assert client.calls == ["ID001"]
    # ...but every area still gets its own finding instead of the group silently vanishing.
    assert [finding.area_symbol for finding in report.findings] == ["ID001", "ID002", "ID003"]
    assert {finding.kind for finding in report.findings} == {"source_query_failed"}


def test_local_candidate_storage_is_read_only(tmp_path: Path) -> None:
    storage = LocalCandidateStorage(tmp_path)
    assert storage.read("soil-survey/candidates/objects/" + "0" * 64, max_bytes=1) is None
    assert storage.read("layer=soil-survey/kind=observed/part-0.parquet", max_bytes=1) is None
    with pytest.raises(SoilSurveyError, match="read-only"):
        storage.put_immutable("soil-survey/candidates/objects/" + "0" * 64, b"x", content_type="application/json")
