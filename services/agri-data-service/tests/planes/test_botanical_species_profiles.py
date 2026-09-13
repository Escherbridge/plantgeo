"""Real immutable Parquet inputs prove bounded botanical serving and honest evidence states."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

from agri_data_service.pipeline.direct.botanical_species_profiles.publication import (
    CURRENT_POINTER_KEY,
    artifact_key,
    build_release,
    publish_release,
)
from agri_data_service.pipeline.direct.botanical_species_profiles.storage import LocalAvailabilityStorage
from agri_data_service.planes import botanical_species_profiles as profiles
from agri_data_service.warehouse.botanical_species_profiles.contract import (
    SECTION_NAMES,
    ReleaseRequest,
    TaxonName,
    TraitAssertion,
    TraitValue,
)
from tests.botanical_species_profiles.fixtures import IDENTITY, make_assertion, make_release_request

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.pipeline.parquet.availability_index import StoredAvailabilityObject
    from agri_data_service.warehouse.botanical_species_profiles.contract import PublishedRelease

FIXTURE_IDENTITY = IDENTITY
FIXTURE_RELEASE_MISSING = "bspf-" + "0" * 64


def _assertion(
    assertion_id: str,
    trait: str,
    raw_value: TraitValue,
    normalized_value: TraitValue | None = None,
) -> TraitAssertion:
    return make_assertion(assertion_id, trait=trait, value=raw_value).model_copy(
        update={
            "normalized_value": normalized_value or raw_value,
            "evidence_kind": "measured_trait" if trait == "heat_content" else "curated_requirement",
        }
    )


def profile_authoring_request() -> ReleaseRequest:
    """Construct attributable synthetic test inputs without admitting a real source."""
    request = make_release_request(
        (
            _assertion("a-growth", "growth_habit", TraitValue(text="forb")),
            _assertion(
                "b-temperature",
                "temperature_envelope",
                TraitValue(lower=32, upper=86, unit="degF"),
                TraitValue(lower=0, upper=30, unit="degC"),
            ),
            _assertion("c-ph", "soil_ph", TraitValue(lower=5, upper=7, unit="pH")),
            _assertion("d-ph-conflict", "soil_ph", TraitValue(lower=6, upper=8, unit="pH")),
            _assertion("e-fuel-without-context", "heat_content", TraitValue(number=18000, unit="kJ/kg")),
            _assertion("f-agricultural-use", "agricultural_use", TraitValue(text="cover crop")),
        )
    )
    cultivar = TaxonName(
        name="Synthetic cultivar",
        source_name_id="cultivar-001",
        accepted_taxon_id=FIXTURE_IDENTITY.taxon_id,
        status="cultivar",
        source_id=request.sources[0].source_id,
    )
    return request.model_copy(update={"taxa": (request.taxa[0].model_copy(update={"cultivars": (cultivar,)}),)})


@dataclass(frozen=True)
class ProfileFixture:
    """One locally published synthetic release shared by transport tests."""

    storage: LocalAvailabilityStorage
    release: PublishedRelease

    @property
    def request(self) -> profiles.ProfileRequest:
        return profiles.ProfileRequest(FIXTURE_IDENTITY, self.release.release_id)

    @property
    def parameters(self) -> dict[str, str]:
        return {**FIXTURE_IDENTITY.model_dump(), "release_id": self.release.release_id}


def publish_profile_fixture(directory: Path, request: ReleaseRequest | None = None) -> ProfileFixture:
    """Write and verify actual Parquet artifacts in an isolated temporary directory."""
    storage = LocalAvailabilityStorage(directory)
    bundle = build_release(request or profile_authoring_request())
    publish_release(storage, bundle, expected_pointer_etag=None)
    return ProfileFixture(storage, bundle.release)


class ReadOnlyProfileStorage:
    """Fail any attempted write and record exactly which immutable objects were read."""

    def __init__(self, storage: LocalAvailabilityStorage) -> None:
        self.storage = storage
        self.reads: list[str] = []

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        self.reads.append(key)
        return self.storage.read(key, max_bytes=max_bytes)

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        raise AssertionError(f"profile serving attempted a write: {key}, {len(payload)}, {content_type}")

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        raise AssertionError(
            f"profile serving attempted a pointer change: {key}, {len(payload)}, {expected_etag}, {content_type}"
        )


def fields_by_trait(payload: dict[str, Any]) -> dict[str, Any]:
    """Index the separate published sections without discarding field evidence."""
    return {field["trait"]: field for section in payload["sections"].values() for field in section["fields"]}


async def test_reads_real_pinned_parquet_with_complete_evidence_and_explicit_gaps(tmp_path: Path) -> None:
    fixture = publish_profile_fixture(tmp_path)
    storage = ReadOnlyProfileStorage(fixture.storage)
    payload = await profiles.read_species_profile(fixture.request, storage=storage)
    fields = fields_by_trait(payload)

    assert payload["state"] == "published"
    assert payload["profile_release_id"] == fixture.release.release_id
    assert payload["taxon_identity"] == FIXTURE_IDENTITY.model_dump()
    assert set(payload["sections"]) == set(SECTION_NAMES)
    assert payload["taxon"]["synonyms"][0]["source_name_id"] == "S-1"
    assert payload["taxon"]["cultivars"][0]["source_name_id"] == "cultivar-001"
    assert fields["growth_habit"]["state"] == "known"
    assert fields["soil_ph"]["state"] == "conflict"
    assert fields["heat_content"]["state"] == "refused"
    assert fields["live_fuel_moisture"]["state"] == "unknown"
    assert fields["fire_tolerance"]["state"] == "unknown"
    assert fields["agricultural_use"]["value"]["text"] == "cover crop"
    temperature = next(row for row in payload["assertions"] if row["trait"] == "temperature_envelope")
    assert temperature["raw_value"]["unit"] == "degF"
    assert temperature["normalized_value"]["unit"] == "degC"
    assert temperature["source_version"] == "v1"
    assert temperature["licence_id"] == "CC0-1.0"
    assert temperature["evidence_locator"] == "record T-1 field temperature_envelope"
    assert payload["release"] == fixture.release.manifest.model_dump(mode="json")
    assert payload["continuation"]["evidence_complete"] is True
    assert set(storage.reads) == {
        artifact_key(fixture.release.release_id, name)
        for name in (
            "manifest",
            "taxa",
            "assertions",
            "decisions",
            "profiles",
        )
    }
    assert CURRENT_POINTER_KEY not in storage.reads
    assert payload["claim_limits"]["species_ranking"] == "refused"
    assert payload["claim_limits"]["establishment_compatibility"] == "not_evaluated"


async def test_assertion_pages_are_bounded_and_cursor_cannot_cross_release_or_taxon(tmp_path: Path) -> None:
    fixture = publish_profile_fixture(tmp_path)
    first = profiles.ProfileRequest(FIXTURE_IDENTITY, fixture.release.release_id, assertion_limit=1)
    payload = await profiles.read_species_profile(first, storage=fixture.storage)
    cursor = payload["continuation"]["next_cursor"]
    assert len(payload["assertions"]) == 1
    assert payload["continuation"]["evidence_complete"] is False
    assert fields_by_trait(payload)["temperature_envelope"]["evidence_complete"] is False
    second = profiles.ProfileRequest(FIXTURE_IDENTITY, fixture.release.release_id, assertion_limit=1, cursor=cursor)
    next_page = await profiles.read_species_profile(second, storage=fixture.storage)
    assert next_page["assertions"][0]["assertion_id"] != payload["assertions"][0]["assertion_id"]
    assert fields_by_trait(next_page)["temperature_envelope"]["evidence_complete"] is True
    with pytest.raises(profiles.ProfileRequestError, match="this exact taxon and release"):
        profiles.ProfileRequest(FIXTURE_IDENTITY, FIXTURE_RELEASE_MISSING, cursor=cursor)
    with pytest.raises(profiles.ProfileRequestError, match="this exact taxon and release"):
        profiles.ProfileRequest(
            FIXTURE_IDENTITY.model_copy(update={"taxon_id": "taxon-002"}), fixture.release.release_id, cursor=cursor
        )


async def test_unknown_taxon_and_unpublished_release_do_not_become_empty_success(tmp_path: Path) -> None:
    fixture = publish_profile_fixture(tmp_path)
    unknown_request = profiles.ProfileRequest(
        FIXTURE_IDENTITY.model_copy(update={"taxon_id": "taxon-absent"}),
        fixture.release.release_id,
    )
    unknown = await profiles.read_species_profile(unknown_request, storage=fixture.storage)
    unpublished = await profiles.read_species_profile(
        profiles.ProfileRequest(FIXTURE_IDENTITY, FIXTURE_RELEASE_MISSING),
        storage=fixture.storage,
    )
    assert unknown["state"] == "unknown"
    assert unknown["reason"]["code"] == "taxon_unknown_in_release"
    assert unknown["release"]["release_id"] == fixture.release.release_id
    assert unpublished["state"] == "refused"
    assert unpublished["reason"]["code"] == "profile_release_unpublished"


@pytest.mark.parametrize("artifact", ["manifest", "taxa", "assertions", "decisions", "profiles"])
async def test_corrupt_stored_artifacts_refuse_without_returning_an_authoring_profile(
    tmp_path: Path, artifact: str
) -> None:
    fixture = publish_profile_fixture(tmp_path)
    (fixture.storage.root / artifact_key(fixture.release.release_id, artifact)).write_bytes(b"corrupt profile bytes")
    result = await profiles.read_species_profile(fixture.request, storage=fixture.storage)
    assert result["state"] == "refused"
    assert result["reason"]["code"] == "profile_release_integrity"
    assert result["taxon"] is None
    assert result["assertions"] == []


@pytest.mark.parametrize(
    "extra",
    [
        {"release_id": "latest"},
        {"release_id": "../escape"},
        {"assertion_limit": "101"},
        {"assertion_limit": "0"},
        {"assertion_limit": "1.5"},
        {"name": "Synthetic test taxon"},
        {"zoom": "13"},
        {"day": "2026-09-11"},
        {"cursor": "arbitrary-cursor"},
    ],
)
def test_malformed_or_implicit_scope_is_rejected_before_storage(extra: dict[str, str]) -> None:
    parameters = {**FIXTURE_IDENTITY.model_dump(), "release_id": FIXTURE_RELEASE_MISSING, **extra}
    with pytest.raises(profiles.ProfileRequestError):
        profiles.parse_profile_request(parameters)


async def test_unconfigured_store_is_explicit_and_does_not_expose_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unconfigured() -> None:
        raise ValueError("private configuration details")

    monkeypatch.setattr(profiles.BotoAvailabilityStorage, "from_settings", unconfigured)
    result = await profiles.read_species_profile(profiles.ProfileRequest(FIXTURE_IDENTITY, FIXTURE_RELEASE_MISSING))
    assert result["reason"]["code"] == "profile_store_unconfigured"
    assert "private configuration details" not in str(result)


async def test_response_size_ceiling_returns_an_explicit_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = publish_profile_fixture(tmp_path)
    monkeypatch.setattr(profiles, "MAX_RESPONSE_BYTES", 1)
    result = await profiles.read_species_profile(fixture.request, storage=fixture.storage)
    assert result["reason"]["code"] == "profile_response_over_budget"
    assert result["assertions"] == []


async def test_oversized_unknown_taxon_manifest_returns_a_small_typed_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = Mock()
    release.lookup.return_value = None
    release.manifest.model_dump.return_value = {
        "release_id": FIXTURE_RELEASE_MISSING,
        "source_releases": [{"review_basis": "x" * profiles.MAX_RESPONSE_BYTES}],
    }
    monkeypatch.setattr(profiles, "read_release", Mock(return_value=release))
    result = await profiles.read_species_profile(
        profiles.ProfileRequest(FIXTURE_IDENTITY, FIXTURE_RELEASE_MISSING),
        storage=LocalAvailabilityStorage(tmp_path),
    )
    assert result["state"] == "refused"
    assert result["reason"]["code"] == "profile_response_over_budget"
    assert result["profile_release_id"] == FIXTURE_RELEASE_MISSING
    assert result["release"] is None
    assert result["taxon"] is None
    assert result["assertions"] == []
    assert len(json.dumps(result).encode()) < profiles.MAX_RESPONSE_BYTES
