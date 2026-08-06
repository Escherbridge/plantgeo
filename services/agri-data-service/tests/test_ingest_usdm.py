"""USDM ingestion: the Tuesday date-walk, the 404 rule, the whole-release rejections, and the in-database repair."""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.ingest.http import UpstreamHttpError, UpstreamPayloadError
from agri_data_service.ingest.usdm import (
    MAX_RETAINED_RELEASES,
    USDM_SOURCE,
    DroughtRelease,
    PostgresDroughtStore,
    fetch_drought_release,
    fetch_latest_drought_release,
    parse_drought_release,
    resolve_retained_releases,
    run_drought_ingestion_job,
    usdm_source_url,
    usdm_valid_date_candidates,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# 2026-08-03 is a Monday, so the most recent published Tuesday is 2026-07-28 -- the release
# production actually holds in geo.drought_areas.
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
LATEST_TUESDAY = "2026-07-28"

SQUARE_RING = [[-113.0, 47.0], [-113.0, 47.1], [-112.9, 47.1], [-112.9, 47.0], [-113.0, 47.0]]

# The whitespace-collapsed in-database geometry repair chain from `drought-ingestion.ts:60-65`.
REPAIR_CHAIN = "ST_Multi( ST_CollectionExtract( ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)), 3 ) )"


class RecordingStore:
    """A drought store that records what the job handed it, so the job's rules need no PostGIS."""

    def __init__(self, written: int = 2, pruned: int = 1) -> None:
        """Configure how many areas a write reports and how many releases a prune removes."""
        self.written = written
        self.pruned = pruned
        self.releases: list[tuple[DroughtRelease, bool]] = []
        self.retentions: list[int] = []

    async def store_release(self, release: DroughtRelease, *, replace: bool) -> int:
        """Record the release and report the configured written count."""
        self.releases.append((release, replace))
        return self.written

    async def prune_releases(self, retain: int) -> int:
        """Record the retention window and report the configured pruned count."""
        self.retentions.append(retain)
        return self.pruned


class FakeResult:
    """The two result accessors the drought store uses: `one()` for a store, `all()` for a prune."""

    def __init__(self, rows: Sequence[Any]) -> None:
        """Hold the rows this statement is pretending to have returned."""
        self._rows = list(rows)

    def all(self) -> list[Any]:
        """Every row the statement returned."""
        return list(self._rows)

    def one(self) -> Any:
        """The single row the store's statement always returns, exactly as SQLAlchemy's `one()` does."""
        if len(self._rows) != 1:
            raise AssertionError(f"expected exactly one row, got {len(self._rows)}")
        return self._rows[0]


class RecordingSession:
    """An AsyncSession stand-in that records the store's own SQL, so the PostGIS port needs no database."""

    def __init__(
        self,
        returned_ids: Sequence[str] = ("d1c0ffee",),
        pruned_dates: Sequence[str] = (),
        fail: bool = False,
        repaired_to_empty: bool = False,
    ) -> None:
        """Configure the rows each insert reports, the prune's rows, whether execution fails, and emptiness."""
        self.returned_ids = list(returned_ids)
        self.pruned_dates = list(pruned_dates)
        self.fail = fail
        self.repaired_to_empty = repaired_to_empty
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: object, parameters: dict[str, Any] | None = None) -> FakeResult:
        """Dispatch on the store's own SQL and return the configured rows."""
        rendered = str(statement)
        self.statements.append((rendered, dict(parameters or {})))
        if self.fail:
            raise SQLAlchemyError("drought write refused")
        if "INSERT INTO geo.drought_areas" in rendered:
            # The store's statement always yields exactly one row carrying the emptiness verdict and
            # the number of rows the guarded insert actually stored.
            return FakeResult(
                [
                    SimpleNamespace(
                        repaired_to_empty=self.repaired_to_empty,
                        rows_stored=0 if self.repaired_to_empty else len(self.returned_ids),
                    )
                ]
            )
        return FakeResult([SimpleNamespace(valid_date=value) for value in self.pruned_dates])

    async def commit(self) -> None:
        """Count a commit."""
        self.commits += 1

    async def rollback(self) -> None:
        """Count a rollback."""
        self.rollbacks += 1


def _collapse_whitespace(sql: str) -> str:
    """Render a SQL statement as a single spaced line so an assertion can pin its clause order."""
    return " ".join(sql.split())


def _feature(drought_class: object, geometry_type: str = "MultiPolygon") -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": geometry_type, "coordinates": [[SQUARE_RING]]},
        "properties": {"DM": drought_class},
    }


def _collection(*drought_classes: int) -> dict[str, object]:
    return {"type": "FeatureCollection", "features": [_feature(item) for item in drought_classes]}


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DROUGHT_RETAINED_RELEASES", raising=False)


def test_the_date_walk_steps_back_over_tuesdays_newest_first() -> None:
    assert usdm_valid_date_candidates(NOW) == ["2026-07-28", "2026-07-21", "2026-07-14"]


def test_a_tuesday_counts_as_its_own_most_recent_release() -> None:
    tuesday = datetime(2026, 7, 28, 6, 0, tzinfo=UTC)
    assert usdm_valid_date_candidates(tuesday, weeks=1) == ["2026-07-28"]


def test_the_candidate_window_is_bounded() -> None:
    assert len(usdm_valid_date_candidates(NOW, weeks=0)) == 1
    assert len(usdm_valid_date_candidates(NOW, weeks=99)) == 8


def test_a_naive_now_is_refused_rather_than_resolved_against_the_container_local_zone() -> None:
    naive = datetime(2026, 8, 3, 12, 0)  # noqa: DTZ001 - the missing zone is the case under test.
    with pytest.raises(ValueError, match="must include a timezone"):
        usdm_valid_date_candidates(naive)


def test_the_source_url_matches_the_provenance_production_stored() -> None:
    assert usdm_source_url(LATEST_TUESDAY) == "https://droughtmonitor.unl.edu/data/json/usdm_20260728.json"


def test_a_release_sorts_its_classes_and_records_its_provenance() -> None:
    release = parse_drought_release(LATEST_TUESDAY, _collection(3, 0, 1))
    assert [area.drought_monitor_category for area in release.areas] == [0, 1, 3]
    assert release.source_url.endswith("usdm_20260728.json")
    assert release.valid_date == LATEST_TUESDAY


def test_the_release_date_is_the_request_parameter_and_never_read_out_of_the_payload() -> None:
    # The published GeoJSON carries no date field, so a date-shaped property must not be believed.
    payload = _collection(0, 1)
    payload["validDate"] = "1999-01-05"
    payload["date"] = "1999-01-05"
    release = parse_drought_release(LATEST_TUESDAY, payload)
    assert release.valid_date == LATEST_TUESDAY
    assert release.source_url.endswith("usdm_20260728.json")


def test_a_repeated_drought_class_rejects_the_whole_release_rather_than_picking_one() -> None:
    with pytest.raises(UpstreamPayloadError, match="repeats drought class D2"):
        parse_drought_release(LATEST_TUESDAY, _collection(0, 2, 2))


def test_a_release_with_no_drought_classes_is_refused() -> None:
    with pytest.raises(UpstreamPayloadError, match="contained no drought classes"):
        parse_drought_release(LATEST_TUESDAY, _collection())


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "Something", "features": []},
        {"type": "FeatureCollection", "features": "not-a-list"},
        {"type": "FeatureCollection", "features": [_feature(9)]},
        {"type": "FeatureCollection", "features": [_feature(True)]},
        {"type": "FeatureCollection", "features": [_feature(1, geometry_type="LineString")]},
        {"type": "FeatureCollection", "features": [_feature(1, geometry_type="GeometryCollection")]},
    ],
)
def test_an_unexpected_release_shape_is_refused(payload: object) -> None:
    with pytest.raises(UpstreamPayloadError):
        parse_drought_release(LATEST_TUESDAY, payload)


def test_a_single_part_polygon_class_is_accepted_rather_than_rejecting_the_whole_release() -> None:
    """USDM ships a contiguous drought class as a bare Polygon; the store's ST_Multi already promotes it.

    This case was pinned as a REFUSAL until 2026-08-05, when it was measured to be the sole cause of 26
    of the 29 release weeks missing from production `geo.drought_areas` -- every one a week whose D4
    class happened to be one contiguous area. See ingest/AGENTS.md "usdm.py".
    """
    release = parse_drought_release(
        LATEST_TUESDAY,
        {
            "type": "FeatureCollection",
            "features": [
                *(_feature(drought_class) for drought_class in (0, 1, 2, 3)),
                _feature(4, geometry_type="Polygon"),
            ],
        },
    )
    assert [area.drought_monitor_category for area in release.areas] == [0, 1, 2, 3, 4]
    assert release.areas[4].geometry["type"] == "Polygon"


async def test_a_non_tuesday_is_refused_before_any_request_is_made() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="Tuesdays"):
            await fetch_drought_release(client, "2026-07-27")


async def test_a_malformed_date_is_refused() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="ISO YYYY-MM-DD"):
            await fetch_drought_release(client, "28-07-2026")


@pytest.mark.parametrize("value", ["20260728", "2026-W31-2"])
async def test_a_date_only_python_accepts_is_refused_like_the_typescript_regex_refuses_it(value: str) -> None:
    # date.fromisoformat() parses both of these to a real Tuesday; `/^\d{4}-\d{2}-\d{2}$/` does not.
    # Without the round-trip spelling check they would pass the Tuesday guard and request a wrong URL.
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="ISO YYYY-MM-DD"):
            await fetch_drought_release(client, value)


async def test_a_404_means_not_published_yet_rather_than_a_failure() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(404))) as client:
        assert await fetch_drought_release(client, LATEST_TUESDAY) is None


async def test_a_non_404_status_still_fails() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(500))) as client:
        with pytest.raises(UpstreamHttpError):
            await fetch_drought_release(client, LATEST_TUESDAY)


async def test_a_garbled_body_stays_a_failure_rather_than_being_read_as_unpublished() -> None:
    # The 404-versus-payload split is why a mid-week walk is normal and a corrupt file is still red.
    response = httpx.Response(200, content=b"<html>maintenance</html>", headers={"content-type": "application/json"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: response)) as client:
        with pytest.raises(UpstreamPayloadError):
            await fetch_drought_release(client, LATEST_TUESDAY)


async def test_the_walk_falls_back_to_the_previous_week_when_this_week_is_unpublished() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if requested[-1].endswith("usdm_20260728.json"):
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=json.dumps(_collection(0, 1)).encode(),
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        release = await fetch_latest_drought_release(client, NOW)
    assert release is not None
    assert release.valid_date == "2026-07-21"
    assert len(requested) == 2
    # usdm_current.json carries no date, so the walk must only ever ask for dated files.
    assert all("usdm_current" not in url for url in requested)


def test_retention_defaults_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_retained_releases() == 8
    monkeypatch.setenv("DROUGHT_RETAINED_RELEASES", "1")
    assert resolve_retained_releases() == 2
    monkeypatch.setenv("DROUGHT_RETAINED_RELEASES", "999")
    assert resolve_retained_releases() == MAX_RETAINED_RELEASES
    # The clamp must clear a full history backfill (~208 weekly releases). Pinned against a
    # literal as well, because a cap below what usdm_history.py writes means no setting can
    # protect the backfill -- that has silently regressed twice.
    assert MAX_RETAINED_RELEASES >= 208
    monkeypatch.setenv("DROUGHT_RETAINED_RELEASES", "12.9")
    assert resolve_retained_releases() == 12
    monkeypatch.setenv("DROUGHT_RETAINED_RELEASES", "nonsense")
    assert resolve_retained_releases() == 8
    monkeypatch.setenv("DROUGHT_RETAINED_RELEASES", "   ")
    assert resolve_retained_releases() == 8


async def test_an_unpublished_week_is_skipped_and_never_failed() -> None:
    store = RecordingStore()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(404))) as client:
        result = await run_drought_ingestion_job(store, client=client, now=NOW)

    assert result.source == USDM_SOURCE
    assert result.status == "skipped"
    assert result.reason == "USDM has not published a release for the requested dates"
    assert store.releases == []
    # Nothing was fetched, so nothing may be pruned -- a skipped week must not age out stored history.
    assert store.retentions == []


async def test_the_job_stores_the_release_then_prunes_old_ones() -> None:
    response = httpx.Response(
        200,
        content=json.dumps(_collection(0, 1)).encode(),
        headers={"content-type": "application/json"},
    )
    store = RecordingStore(written=2, pruned=3)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: response)) as client:
        result = await run_drought_ingestion_job(store, client=client, now=NOW)

    assert result.status == "ingested"
    assert result.records_seen == 2
    assert result.records_written == 2
    assert result.details["releases_pruned"] == 3
    assert store.retentions == [8]
    stored_release, replace = store.releases[0]
    assert stored_release.valid_date == LATEST_TUESDAY
    assert replace is False


async def test_replaying_a_stored_release_writes_nothing_and_is_still_ingested() -> None:
    # Idempotence: the weekly cron re-reads the same release all week and must stay green at zero writes.
    response = httpx.Response(
        200,
        content=json.dumps(_collection(0, 1)).encode(),
        headers={"content-type": "application/json"},
    )
    store = RecordingStore(written=0, pruned=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: response)) as client:
        result = await run_drought_ingestion_job(store, client=client, now=NOW)

    assert result.status == "ingested"
    assert result.records_seen == 2
    assert result.records_written == 0


async def test_an_explicit_release_date_bypasses_the_walk() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            200,
            content=json.dumps(_collection(0)).encode(),
            headers={"content-type": "application/json"},
        )

    store = RecordingStore()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_drought_ingestion_job(store, valid_date="2026-07-14", replace=True, client=client)

    assert result.status == "ingested"
    assert len(requested) == 1
    assert store.releases[0][1] is True


async def test_the_store_repairs_geometry_in_the_database_and_never_in_python() -> None:
    session = RecordingSession()
    release = parse_drought_release(LATEST_TUESDAY, _collection(0))
    written = await PostgresDroughtStore(session).store_release(release, replace=False)

    assert written == 1
    rendered, parameters = session.statements[0]
    assert REPAIR_CHAIN in _collapse_whitespace(rendered)
    # The geometry crosses the boundary as GeoJSON text for PostGIS to repair, never as a Python shape.
    assert isinstance(parameters["geometry"], str)
    assert json.loads(parameters["geometry"]) == {"type": "MultiPolygon", "coordinates": [[SQUARE_RING]]}
    assert parameters["valid_date"] == LATEST_TUESDAY
    assert parameters["dm_category"] == 0
    assert parameters["source_url"].endswith("usdm_20260728.json")


@pytest.mark.parametrize(("replace", "predicate"), [(False, "WHERE false"), (True, "WHERE true")])
async def test_the_conflict_predicate_is_what_makes_a_re_run_a_no_op(replace: bool, predicate: str) -> None:
    session = RecordingSession()
    release = parse_drought_release(LATEST_TUESDAY, _collection(0))
    await PostgresDroughtStore(session).store_release(release, replace=replace)

    collapsed = _collapse_whitespace(session.statements[0][0])
    assert "ON CONFLICT (valid_date, dm_category) DO UPDATE" in collapsed
    assert predicate in collapsed
    assert "RETURNING id" in collapsed


async def test_a_class_whose_geometry_repairs_to_empty_is_refused_rather_than_stored() -> None:
    """`geo.drought_areas.geom` is NOT NULL and MULTIPOLYGON EMPTY satisfies it, claiming coverage of nothing."""
    session = RecordingSession(repaired_to_empty=True)
    release = parse_drought_release(LATEST_TUESDAY, _collection(0))

    with pytest.raises(UpstreamPayloadError, match="repaired to an empty geometry"):
        await PostgresDroughtStore(session).store_release(release, replace=False)

    collapsed = _collapse_whitespace(session.statements[0][0])
    # The guard is in the statement as well as in the reaction: an empty repair inserts no row at all.
    assert "WHERE NOT ST_IsEmpty(repaired.geom)" in collapsed
    assert session.commits == 0
    assert session.rollbacks == 1


async def test_a_release_is_written_in_one_transaction_and_counts_only_the_rows_returning() -> None:
    session = RecordingSession(returned_ids=())
    release = parse_drought_release(LATEST_TUESDAY, _collection(0, 1, 2))
    written = await PostgresDroughtStore(session).store_release(release, replace=False)

    # Three areas, each conflicting under `WHERE false`, so RETURNING yields nothing.
    assert written == 0
    assert len(session.statements) == 3
    assert session.commits == 1
    assert session.rollbacks == 0


async def test_a_failed_write_rolls_back_and_propagates_rather_than_reporting_a_partial_count() -> None:
    session = RecordingSession(fail=True)
    release = parse_drought_release(LATEST_TUESDAY, _collection(0))
    with pytest.raises(SQLAlchemyError):
        await PostgresDroughtStore(session).store_release(release, replace=False)

    assert session.commits == 0
    assert session.rollbacks == 1


async def test_the_prune_keeps_the_newest_releases_and_counts_releases_not_rows() -> None:
    session = RecordingSession(pruned_dates=["2026-06-02", "2026-06-02", "2026-05-26"])
    pruned = await PostgresDroughtStore(session).prune_releases(8)

    # Two rows share a valid_date; the operator is told two releases went, not three rows.
    assert pruned == 2
    rendered, parameters = session.statements[0]
    collapsed = _collapse_whitespace(rendered)
    assert collapsed.startswith("DELETE FROM geo.drought_areas WHERE valid_date NOT IN (")
    assert "SELECT valid_date FROM geo.drought_areas GROUP BY valid_date ORDER BY valid_date DESC" in collapsed
    assert "LIMIT :retain" in collapsed
    assert collapsed.endswith("RETURNING valid_date")
    assert parameters["retain"] == 8
    assert session.commits == 1
