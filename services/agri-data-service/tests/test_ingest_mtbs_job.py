"""Tests for the `ingest-mtbs` job: release-dated writes into the burn-severity layer, no fabricated dates.

Database-free by construction -- this module never requests the ``agri_db`` fixtures, so it can
never contribute a silent skip to the sweep gate in ``conftest.pytest_sessionfinish``. The capture
path (payload plus reviewed sidecar to disk) is pinned by ``test_ingest_mtbs.py``; this module pins
the second entry point, which writes the same cohorts through the shared feature writer.
"""

# ruff: noqa: PLR2004

import asyncio
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from agri_data_service.ingest.mtbs import (
    DEFAULT_BURN_SEVERITY_LAYER_NAME,
    MTBS_ANNUAL_RELEASE_DATES,
    MTBS_CHANNEL,
    MTBS_SOURCE,
    MtbsReleaseNotPublishedError,
    build_mtbs_record,
    build_mtbs_source,
    build_mtbs_write,
    resolve_burn_severity_layer_name,
    resolve_data_available_at,
    resolve_release_years,
    run_mtbs_ingestion_job,
)
from agri_data_service.ingest.source import FetchRequest, HistoryUnavailableError, HistoryWindow
from agri_data_service.ingest.writer import FeatureWrite
from agri_data_service.interface.cli import cli

PACIFIC_NORTHWEST_BBOX_TEXT = "-125,42,-111,49"

# Two release cohorts whose publication dates are ordered (2021 -> 2023-08-09, 2022 -> 2024-08-22),
# which is what lets the truncation test prove the cap drops the OLDEST release rather than a slice
# of whichever cohort happened to be fetched last.
COHORT_YEARS = (2021, 2022)


def _properties(fire_identifier: str, ignition_year: int, ignition_date: int) -> dict[str, Any]:
    """A lowercase EDW property bag, the spelling the live service actually publishes."""
    return {
        "fire_id": fire_identifier,
        "fire_name": "POWERLINE",
        "year": ignition_year,
        "fire_type": "Wildfire",
        "acres": 1100.0,
        "map_id": 10029267,
        "asmnt_type": "Initial",
        "ig_date": ignition_date,
        "pre_id": "804003020220904",
        "post_id": "804003020221006",
        "perim_id": None,
        "dnbr_offst": -1,
        "dnbr_stddv": 16,
        "nodata_threshold": -970,
        "greenness_threshold": -150,
        "low_threshold": 70,
        "moderate_threshold": 9999,
        "high_threshold": 9999,
    }


def _polygon(index: int) -> dict[str, Any]:
    """A small valid closed ring; a real perimeter carries thousands of vertices."""
    west = -120.0 + index * 0.1
    south = 44.0 + index * 0.1
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [west + 0.05, south],
                [west + 0.05, south + 0.05],
                [west, south + 0.05],
                [west, south],
            ]
        ],
    }


def _feature(properties: dict[str, Any], index: int) -> dict[str, Any]:
    return {"type": "Feature", "geometry": _polygon(index), "properties": dict(properties)}


def _cohort(ignition_year: int, size: int) -> list[dict[str, Any]]:
    """One fire year's features, ordered by `fire_id` the way `orderByFields` returns them."""
    return [
        _feature(
            _properties(f"ID42{index:03d}114403{ignition_year}0906", ignition_year, ignition_year * 10000 + 906), index
        )
        for index in range(size)
    ]


class RecordedMtbsService:
    """A recorded EDW feature service that answers per fire year, in pages, with a count gate."""

    def __init__(self, cohorts: dict[int, list[dict[str, Any]]]) -> None:
        self.cohorts = cohorts
        self.requested_years: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        parameters = dict(request.url.params)
        ignition_year = int(parameters["where"].split("=")[-1].strip())
        features = self.cohorts.get(ignition_year, [])
        if parameters.get("returnCountOnly") == "true":
            self.requested_years.append(ignition_year)
            return httpx.Response(200, json={"count": len(features)}, request=request)
        offset = int(parameters["resultOffset"])
        page_size = int(parameters["resultRecordCount"])
        page = features[offset : offset + page_size]
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": page,
                "properties": {"exceededTransferLimit": offset + page_size < len(features)},
            },
            request=request,
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


class RecordingWriter:
    """The `FeatureWriter` seam, recorded rather than persisted, so a job test needs no database."""

    def __init__(self) -> None:
        self.writes: list[FeatureWrite] = []

    async def __call__(self, writes: list[FeatureWrite]) -> int:
        self.writes.extend(writes)
        return len(writes)


def _run_job(
    service: RecordedMtbsService,
    writer: RecordingWriter,
    *,
    bbox: str | None = PACIFIC_NORTHWEST_BBOX_TEXT,
    release_years: list[int] | None = None,
) -> Any:
    async def run() -> Any:
        async with service.client() as client:
            return await run_mtbs_ingestion_job(
                writer,
                bbox=bbox,
                release_years=release_years if release_years is not None else list(COHORT_YEARS),
                client=client,
            )

    return asyncio.run(run())


def _build_write(ignition_year: int = 2022) -> FeatureWrite:
    record = build_mtbs_record(
        _feature(_properties("ID4262711440320220906", ignition_year, 20220906), 0), ignition_year
    )
    write = build_mtbs_write({"record": record}, FetchRequest(bbox=PACIFIC_NORTHWEST_BBOX_TEXT, max_records=10))
    assert write is not None
    return write


def test_the_ingest_mtbs_verb_is_registered_on_the_cli() -> None:
    # The module was complete and imported by nothing; a registered verb is what makes it reachable.
    help_output = CliRunner().invoke(cli, ["data", "ingest-mtbs", "--help"])
    assert help_output.exit_code == 0
    assert "--release-year" in help_output.output
    assert "--bbox" in help_output.output


def test_an_unconfigured_bbox_skips_rather_than_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    # An unpointed deployment is not a broken upstream; a failure here would make every
    # unconfigured environment's cron run red.
    monkeypatch.delenv("INGEST_BBOX", raising=False)
    writer = RecordingWriter()
    result = asyncio.run(run_mtbs_ingestion_job(writer, bbox=None))
    assert result.status == "skipped"
    assert result.source == MTBS_SOURCE
    assert writer.writes == []


def test_every_cohort_is_paged_to_completion_and_written_through_the_shared_writer() -> None:
    service = RecordedMtbsService({2021: _cohort(2021, 5), 2022: _cohort(2022, 4)})
    writer = RecordingWriter()

    result = _run_job(service, writer)

    assert result.status == "ingested"
    assert result.source == MTBS_SOURCE
    assert result.records_seen == 9
    assert result.records_written == 9
    assert result.truncated is False
    assert result.details == {"releases": 2, "rejected": 0}
    # The count gate is asked once per cohort, before that cohort is paged.
    assert service.requested_years == [2021, 2022]
    assert len(writer.writes) == 9
    assert {write.layer_reference for write in writer.writes} == {DEFAULT_BURN_SEVERITY_LAYER_NAME}
    assert {write.channel for write in writer.writes} == {MTBS_CHANNEL}
    assert len({write.external_id for write in writer.writes}) == 9


def test_a_write_is_dated_by_its_release_publication_and_never_by_its_ignition() -> None:
    # An ignition date as the observation instant leaks the whole ~18-month mapping lag into every
    # model that reads this layer, invisibly. `observedAt` and the geometry version boundary are the
    # same instant by construction, so the two can never disagree.
    write = _build_write(2022)
    release = resolve_data_available_at(2022)
    assert write.identity.observed_at == release
    assert write.properties["observedAt"] == "2024-08-22T00:00:00.000Z"
    assert write.properties["ignitionDate"] == "2022-09-06"
    assert write.properties["observedAt"] != write.properties["ignitionDate"]


def test_a_write_keys_through_the_identity_contract_and_carries_the_type_two_change_signal() -> None:
    write = _build_write(2022)
    assert write.identity.producer == "mtbs"
    assert write.natural_key == "mtbs:ID4262711440320220906"
    assert write.external_id == "ID4262711440320220906"
    # Versioning is on MTBS's own mapping identifiers, never on geometry floats.
    assert (
        write.properties["mappingRevision"]
        == "mtbs-2022-release-2024-08-22|10029267|Initial|804003020220904|804003020221006|"
    )
    assert write.properties["releaseIdentifier"] == "mtbs-2022-release-2024-08-22"
    assert write.properties["geometry"] == _polygon(0)
    assert write.grid_cell is None


def test_an_absent_severity_attribute_is_stored_as_none_and_never_as_unburned() -> None:
    # `unburned` is a real class (code 1), so a fallback to it mislabels every feature the polygon
    # layer publishes -- and the polygon layer publishes no severity attribute at all.
    write = _build_write(2022)
    assert write.properties["severityClass"] is None
    assert write.properties["severityThresholds"]["low_threshold"] == 70


def test_the_layer_name_is_read_at_call_time_so_a_cron_change_needs_no_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert resolve_burn_severity_layer_name() == DEFAULT_BURN_SEVERITY_LAYER_NAME
    monkeypatch.setenv("BURN_SEVERITY_LAYER_ID", "burn-severity-staging")
    assert resolve_burn_severity_layer_name() == "burn-severity-staging"
    assert _build_write(2022).layer_reference == "burn-severity-staging"
    monkeypatch.setenv("BURN_SEVERITY_LAYER_ID", "   ")
    assert resolve_burn_severity_layer_name() == DEFAULT_BURN_SEVERITY_LAYER_NAME


def test_a_fire_year_with_no_established_release_date_fails_the_run_rather_than_borrowing_one() -> None:
    # 2023 onward is still being mapped by MTBS. There is no fallback: an ignition date, `now()` or
    # an assumed mapping lag would each silently backdate what the warehouse claims it could know.
    assert 2023 not in MTBS_ANNUAL_RELEASE_DATES
    service = RecordedMtbsService({2023: _cohort(2023, 2)})
    with pytest.raises(MtbsReleaseNotPublishedError):
        _run_job(service, RecordingWriter(), release_years=[2023])


def test_a_bitten_cap_drops_the_oldest_release_and_never_an_arrival_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cohorts are fetched oldest year first, so an arrival slice would keep 2021 and discard 2022.
    # The shared `select_writes` rule keeps the newest observations, which here means the newest
    # RELEASE -- the cohort a consumer is least able to reconstruct from anywhere else.
    monkeypatch.setattr("agri_data_service.ingest.mtbs.resolve_max_source_records", lambda: 3)
    service = RecordedMtbsService({2021: _cohort(2021, 3), 2022: _cohort(2022, 3)})
    writer = RecordingWriter()

    result = _run_job(service, writer)

    assert result.records_seen == 6
    assert result.records_written == 3
    assert result.truncated is True
    assert {write.properties["fireYear"] for write in writer.writes} == {2022}


def test_the_source_refuses_history_in_typed_terms_rather_than_walking_it_emptily() -> None:
    # A `fetch_history` returning [] is a backfill that walks two years, writes nothing and reports
    # a clean run -- which is how a gap gets certified as complete.
    source = build_mtbs_source()
    capability = source.history_capability()
    assert capability.supported is False
    assert "governance gap" in (capability.reason or "")
    window = HistoryWindow(start=resolve_data_available_at(2018), end=resolve_data_available_at(2022))
    with pytest.raises(HistoryUnavailableError):
        asyncio.run(source.fetch_history(FetchRequest(bbox=PACIFIC_NORTHWEST_BBOX_TEXT, max_records=10), window))


def test_release_years_default_to_every_year_with_an_established_publication_date() -> None:
    assert resolve_release_years(None) == sorted(MTBS_ANNUAL_RELEASE_DATES)
    assert resolve_release_years([]) == sorted(MTBS_ANNUAL_RELEASE_DATES)
    assert resolve_release_years([2022, 2021, 2022]) == [2021, 2022]
