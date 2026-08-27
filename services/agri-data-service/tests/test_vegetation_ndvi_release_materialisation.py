"""The governed NDVI plane must measure what its own cells landed, not what one pass inserted.

The refusal itself is a pure function of measured counts, so both its branches are tested here
without a database, matching `unanswered_open_meteo_parameters`. The parts that need a real
planner -- that the SQL executes, that the guard fires end to end, that the raise unwinds its own
release -- live in `test_vegetation_ndvi_plane_postgresql.py`, because a stubbed session cannot
prove any of them.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast

import pytest

from agri_data_service.cli import _registration_payload
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution import vegetation_ndvi_plane as plane_module
from agri_data_service.execution.vegetation_ndvi_plane import (
    EMPTY_RELEASE_REASON,
    EMPTY_SELECTION_REASON,
    CorpusChangedDuringRegistrationError,
    EmptyGovernedReleaseError,
    GovernedPlane,
    RegistrationSummary,
    ReleaseMaterialisation,
    SelectionMaterialisation,
    all_requested_cells_materialised,
    empty_materialisation_reason,
    measure_release_materialisation,
    register_governed_forward_plane,
    release_holds_claimed_corpus,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_RELEASE_ID = uuid.UUID("3506804b-3639-4aa6-bed9-bfa928abca55")
_CUTOFF_DAY = date(2026, 8, 8)
_FIRST_DAY = date(2022, 8, 5)
# The shape of the one governed release production actually holds, so a regression reads as a
# departure from measured reality rather than from an invented fixture.
_CORPUS_CELL_COUNT = 1568
_CORPUS_CELL_DAY_COUNT = 184_409
_PARTIAL_SERIES_COUNT = 24
_PARTIAL_CELL_DAY_COUNT = 2_800

_FULL_RELEASE = ReleaseMaterialisation(
    observation_count=_CORPUS_CELL_DAY_COUNT,
    series_count=_CORPUS_CELL_COUNT,
    first_observed_day=_FIRST_DAY,
    last_observed_day=_CUTOFF_DAY,
)
_EMPTY_RELEASE = ReleaseMaterialisation(
    observation_count=0,
    series_count=0,
    first_observed_day=None,
    last_observed_day=None,
)


class _FakeResult:
    """The narrow slice of SQLAlchemy's Result this reader uses."""

    def __init__(self, row: Mapping[str, object]) -> None:
        self._row = row

    def mappings(self) -> _FakeResult:
        """Return self, because the fake already yields mappings."""
        return self

    def one(self) -> Mapping[str, object]:
        """The single aggregate row, which this statement always produces."""
        return self._row


class _AggregateSession:
    """An AsyncSession stand-in that answers the materialisation read and checks its binds."""

    def __init__(self, row: Mapping[str, object]) -> None:
        self._row = row
        self.parameters: dict[str, object] = {}

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> _FakeResult:
        """Refuse a bind mismatch the way a real driver would, then answer from the script."""
        bound = set(getattr(statement, "_bindparams", {}) or {})
        supplied = set(parameters or {})
        if bound != supplied:
            raise AssertionError(f"statement binds {sorted(bound)} but was given {sorted(supplied)}")
        self.parameters = dict(parameters or {})
        return _FakeResult(self._row)


def _plane() -> GovernedPlane:
    return GovernedPlane(
        data_source_id=uuid.uuid4(),
        source_release_id=_RELEASE_ID,
        release_set_id=uuid.uuid4(),
        release_manifest_checksum="b" * 64,
        payload_checksum="a" * 64,
        corpus_cell_count=_CORPUS_CELL_COUNT,
        corpus_cell_day_count=_CORPUS_CELL_DAY_COUNT,
        corpus_row_count=_CORPUS_CELL_DAY_COUNT,
        first_observed_day=_FIRST_DAY,
        last_observed_day=_CUTOFF_DAY,
    )


def _summary(
    materialisation: ReleaseMaterialisation,
    selection: SelectionMaterialisation,
    *,
    requested_cell_count: int = _CORPUS_CELL_COUNT,
) -> RegistrationSummary:
    return RegistrationSummary(
        plane=_plane(),
        requested_cell_count=requested_cell_count,
        spatial_cell_count=0,
        series_count=0,
        observation_count=0,
        materialisation=materialisation,
        selection=selection,
    )


def _selected_aliases(query_path: str) -> set[str]:
    """Return every `AS <name>` in one statement's body: column aliases, but table aliases and cast
    types too (`observation`, `series`, `cell`, `text`, `varchar` all appear).

    Deliberately loose, and sound only because callers assert a SUBSET. That still closes the hole
    it exists for -- renaming a selected column's alias removes it from this set and fails the
    assertion -- while staying immune to the prose header, which is stripped first.
    """
    body = "\n".join(line for line in load_query_sql(query_path).splitlines() if not line.lstrip().startswith("--"))
    return set(re.findall(r"\bAS\s+([a-z_]+)\b", body))


# --- the refusal predicate: every branch, no database -------------------------------------------


def test_a_selection_that_landed_rows_is_not_refused() -> None:
    reason = empty_materialisation_reason(
        selection=SelectionMaterialisation(observation_count=_PARTIAL_CELL_DAY_COUNT, series_count=24),
        materialisation=_FULL_RELEASE,
    )
    assert reason is None


def test_a_pass_whose_own_cells_landed_nothing_is_refused_even_when_the_release_is_full() -> None:
    """The reachable bug: cells resolve to nothing, an earlier pass's rows still fill the release."""
    reason = empty_materialisation_reason(
        selection=SelectionMaterialisation(observation_count=0, series_count=0),
        materialisation=_FULL_RELEASE,
    )
    assert reason == EMPTY_SELECTION_REASON


def test_a_pass_against_a_wholly_empty_release_names_the_wider_failure() -> None:
    reason = empty_materialisation_reason(
        selection=SelectionMaterialisation(observation_count=0, series_count=0),
        materialisation=_EMPTY_RELEASE,
    )
    assert reason == EMPTY_RELEASE_REASON


def test_the_release_wide_count_alone_cannot_distinguish_the_two_refusal_cases() -> None:
    """Pins the reason the gate is selection-scoped: identical release counts, opposite verdicts."""
    landed = empty_materialisation_reason(
        selection=SelectionMaterialisation(observation_count=_CORPUS_CELL_DAY_COUNT, series_count=_CORPUS_CELL_COUNT),
        materialisation=_FULL_RELEASE,
    )
    unlanded = empty_materialisation_reason(
        selection=SelectionMaterialisation(observation_count=0, series_count=0),
        materialisation=_FULL_RELEASE,
    )
    assert landed is None
    assert unlanded == EMPTY_SELECTION_REASON


# --- the two completeness predicates, which answer different questions ---------------------------


def test_release_holds_claimed_corpus_is_true_only_for_the_whole_fingerprinted_corpus() -> None:
    assert release_holds_claimed_corpus(materialisation=_FULL_RELEASE, plane=_plane()) is True


def test_release_holds_claimed_corpus_goes_false_when_a_cell_is_below_the_candidate_threshold() -> None:
    """A cell with too few observed days is fingerprinted but never materialisable, so this is false."""
    below_threshold = ReleaseMaterialisation(
        observation_count=_CORPUS_CELL_DAY_COUNT,
        series_count=_CORPUS_CELL_COUNT - 1,
        first_observed_day=_FIRST_DAY,
        last_observed_day=_CUTOFF_DAY,
    )
    assert release_holds_claimed_corpus(materialisation=below_threshold, plane=_plane()) is False


def test_all_requested_cells_materialised_is_the_luck_free_per_pass_signal() -> None:
    selection = SelectionMaterialisation(observation_count=_PARTIAL_CELL_DAY_COUNT, series_count=_PARTIAL_SERIES_COUNT)
    assert all_requested_cells_materialised(selection=selection, requested_cell_count=_PARTIAL_SERIES_COUNT) is True
    assert all_requested_cells_materialised(selection=selection, requested_cell_count=_CORPUS_CELL_COUNT) is False


# --- the readers and the payload ------------------------------------------------------------------


def test_the_statements_really_expose_the_columns_their_readers_index_by() -> None:
    """Guards the alias-drift hole a hand-authored row dict cannot see: rename an alias, fail here."""
    assert {"observation_count", "series_count", "first_observed_at", "last_observed_at"} <= _selected_aliases(
        "execution/release_materialisation.sql"
    )
    assert {"observation_count", "series_count"} <= _selected_aliases("execution/selection_materialisation.sql")


@pytest.mark.asyncio
async def test_materialisation_reads_the_release_rather_than_the_insert_count() -> None:
    session = _AggregateSession(
        {
            "observation_count": _CORPUS_CELL_DAY_COUNT,
            "series_count": _CORPUS_CELL_COUNT,
            "first_observed_at": datetime(2022, 8, 5, tzinfo=UTC),
            "last_observed_at": datetime(2026, 8, 8, tzinfo=UTC),
        }
    )
    measured = await measure_release_materialisation(cast("Any", session), source_release_id=_RELEASE_ID)
    assert session.parameters == {"source_release_id": _RELEASE_ID}
    assert measured.observation_count == _CORPUS_CELL_DAY_COUNT
    assert measured.series_count == _CORPUS_CELL_COUNT
    assert measured.first_observed_day == _FIRST_DAY
    assert measured.last_observed_day == _CUTOFF_DAY


@pytest.mark.asyncio
async def test_an_empty_release_reads_back_as_zero_and_not_as_a_missing_row() -> None:
    session = _AggregateSession(
        {"observation_count": 0, "series_count": 0, "first_observed_at": None, "last_observed_at": None}
    )
    measured = await measure_release_materialisation(cast("Any", session), source_release_id=_RELEASE_ID)
    assert measured.observation_count == 0
    assert measured.first_observed_day is None
    assert measured.last_observed_day is None


def test_the_refusal_carries_the_evidence_that_explains_it() -> None:
    error = EmptyGovernedReleaseError(
        reason_code=EMPTY_SELECTION_REASON,
        cutoff_day=_CUTOFF_DAY,
        requested_cell_count=_PARTIAL_SERIES_COUNT,
        release_observation_count=_CORPUS_CELL_DAY_COUNT,
    )
    assert error.reason_code == EMPTY_SELECTION_REASON
    assert error.requested_cell_count == _PARTIAL_SERIES_COUNT
    assert error.release_observation_count == _CORPUS_CELL_DAY_COUNT


def test_registration_payload_separates_the_per_pass_signal_from_the_whole_corpus_one() -> None:
    payload = _registration_payload(
        _summary(
            _FULL_RELEASE,
            SelectionMaterialisation(
                observation_count=_PARTIAL_CELL_DAY_COUNT,
                series_count=_PARTIAL_SERIES_COUNT,
            ),
            requested_cell_count=_PARTIAL_SERIES_COUNT,
        )
    )
    # The release is the whole corpus, yet this pass covered only its own 24 cells: both true.
    assert payload["release_matches_claimed_corpus"] is True
    assert payload["requested_cells_materialised"] == _PARTIAL_SERIES_COUNT
    assert payload["requested_cell_days_materialised"] == _PARTIAL_CELL_DAY_COUNT
    assert payload["all_requested_cells_materialised"] is True
    assert payload["release_cell_day_count"] == _CORPUS_CELL_DAY_COUNT
    assert payload["release_last_observed_day"] == "2026-08-08"


def test_registration_payload_reports_a_pass_that_covered_less_than_it_asked_for() -> None:
    payload = _registration_payload(
        _summary(
            _FULL_RELEASE,
            SelectionMaterialisation(
                observation_count=_PARTIAL_CELL_DAY_COUNT,
                series_count=_PARTIAL_SERIES_COUNT,
            ),
            requested_cell_count=_CORPUS_CELL_COUNT,
        )
    )
    assert payload["all_requested_cells_materialised"] is False
    assert payload["requested_cells_materialised"] == _PARTIAL_SERIES_COUNT


@pytest.mark.asyncio
async def test_forward_registration_redigests_and_refuses_a_concurrent_raw_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = plane_module._CorpusDigest(
        payload_checksum="a" * 64,
        cell_count=1,
        cell_day_count=1,
        row_count=1,
        first_observed_day=_CUTOFF_DAY,
        last_observed_day=_CUTOFF_DAY,
    )
    after = plane_module._CorpusDigest(
        payload_checksum="b" * 64,
        cell_count=1,
        cell_day_count=1,
        row_count=2,
        first_observed_day=_CUTOFF_DAY,
        last_observed_day=_CUTOFF_DAY,
    )
    digests = iter((before, after))

    async def pin(*_args: object, **_kwargs: object) -> None:
        return None

    async def digest(*_args: object, **_kwargs: object) -> plane_module._CorpusDigest:
        return next(digests)

    async def register_id(*_args: object, **_kwargs: object) -> uuid.UUID:
        return _RELEASE_ID

    async def register_set(*_args: object, **_kwargs: object) -> tuple[uuid.UUID, str]:
        return uuid.uuid4(), "c" * 64

    async def register_count(*_args: object, **_kwargs: object) -> int:
        return 1

    monkeypatch.setattr(plane_module, "pin_determinism", pin)
    monkeypatch.setattr(plane_module, "advisory_lock", pin)
    monkeypatch.setattr(plane_module, "_corpus_digest", digest)
    monkeypatch.setattr(plane_module, "_register_data_source", register_id)
    monkeypatch.setattr(plane_module, "_register_source_release", register_id)
    monkeypatch.setattr(plane_module, "_register_release_set", register_set)
    monkeypatch.setattr(plane_module, "_register_spatial_cells", register_count)
    monkeypatch.setattr(plane_module, "_register_series", register_count)
    monkeypatch.setattr(plane_module, "_load_observations", register_count)

    with pytest.raises(CorpusChangedDuringRegistrationError, match="changed while"):
        await register_governed_forward_plane(
            cast("Any", object()),
            cutoff_day=_CUTOFF_DAY,
            cell_days=(("43.1250:-116.1250", _CUTOFF_DAY),),
        )
