"""Bounded, release-pinned botanical lookups; see `AGENTS.md` for the static exception."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from agri_data_service.parquet_ops.duckdb_session import run_bounded_read
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.pipeline.direct.botanical_species_profiles.publication import read_release
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityMalformedError,
    AvailabilityUnavailableError,
    BotoAvailabilityStorage,
)
from agri_data_service.warehouse.botanical_species_profiles.contract import (
    SECTION_NAMES,
    ProfileError,
    ProfileUnavailableError,
    TaxonIdentity,
    require_release_id,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.warehouse.botanical_species_profiles.contract import PublishedRelease, SpeciesProfile

PRODUCT: Final = "botanical-species-profile"
DEFAULT_ASSERTION_LIMIT: Final = 25
MAX_ASSERTION_LIMIT: Final = 100
MAX_IDENTITY_LENGTH: Final = 200
MAX_CURSOR_LENGTH: Final = 80
MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
READ_TIMEOUT_SECONDS: Final = 14.0
_PARAMETERS: Final = frozenset(
    {"authority", "authority_version", "taxon_id", "release_id", "assertion_limit", "cursor"}
)
_CURSOR_PATTERN: Final = re.compile(r"(?P<offset>[1-9][0-9]{0,8})\.(?P<binding>[0-9a-f]{64})")


class ProfileRequestError(ValueError):
    """A malformed or unpinned botanical lookup request."""


@dataclass(frozen=True, slots=True)
class ProfileRequest:
    """One exact taxon concept and immutable release, with a bounded evidence page."""

    identity: TaxonIdentity
    release_id: str
    assertion_limit: int = DEFAULT_ASSERTION_LIMIT
    cursor: str | None = None

    def __post_init__(self) -> None:
        try:
            require_release_id(self.release_id)
        except ProfileError as error:
            raise ProfileRequestError(
                "release_id must be bspf- followed by the exact lowercase SHA-256 digest"
            ) from error
        if type(self.assertion_limit) is not int or not 1 <= self.assertion_limit <= MAX_ASSERTION_LIMIT:
            raise ProfileRequestError(f"assertion_limit must be an integer from 1 to {MAX_ASSERTION_LIMIT}")
        if self.cursor is not None:
            _cursor_offset(self)


def parse_profile_request(parameters: Mapping[str, str]) -> ProfileRequest:
    """Validate a canonical, nonspatial lookup without accepting name or latest aliases."""
    if set(parameters) - _PARAMETERS:
        raise ProfileRequestError(
            "unsupported parameters; this lookup accepts only a canonical taxon and pinned release"
        )
    identity_values = {
        field: _identity_parameter(parameters, field) for field in ("authority", "authority_version", "taxon_id")
    }
    release_id = _identity_parameter(parameters, "release_id")
    raw_limit = parameters.get("assertion_limit", str(DEFAULT_ASSERTION_LIMIT))
    if not raw_limit.isascii() or not raw_limit.isdecimal() or len(raw_limit) > len(str(MAX_ASSERTION_LIMIT)):
        raise ProfileRequestError("assertion_limit must be an integer")
    try:
        identity = TaxonIdentity(**identity_values)
    except ValueError as error:
        raise ProfileRequestError(
            "authority, authority_version and taxon_id must identify one canonical taxon"
        ) from error
    return ProfileRequest(identity, release_id, int(raw_limit), parameters.get("cursor"))


def _identity_parameter(parameters: Mapping[str, str], name: str) -> str:
    value = parameters.get(name)
    if value is None or not value or value != value.strip() or len(value) > MAX_IDENTITY_LENGTH:
        raise ProfileRequestError(f"{name} is required and must be an exact bounded identifier")
    return value


def _cursor_binding(request: ProfileRequest) -> str:
    payload = {"release_id": request.release_id, "identity": request.identity.model_dump(mode="json"), "version": 1}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cursor_offset(request: ProfileRequest) -> int:
    if request.cursor is None:
        return 0
    match = _CURSOR_PATTERN.fullmatch(request.cursor) if len(request.cursor) <= MAX_CURSOR_LENGTH else None
    if match is None or match["binding"] != _cursor_binding(request):
        raise ProfileRequestError("cursor must be a continuation for this exact taxon and release")
    return int(match["offset"])


def _empty_result(request: ProfileRequest) -> dict[str, Any]:
    return {
        "product": PRODUCT,
        "profile_release_id": request.release_id,
        "taxon_identity": request.identity.model_dump(mode="json"),
        "serving_source": "immutable_parquet",
        "taxon": None,
        "sections": {name: {"state": "unavailable", "fields": []} for name in SECTION_NAMES},
        "release": None,
        "assertions": [],
        "decisions": [],
        "continuation": {
            "assertion_limit": request.assertion_limit,
            "assertions_returned": 0,
            "assertions_total": None,
            "next_cursor": None,
            "evidence_complete": False,
        },
        "claim_limits": {
            "establishment_compatibility": "not_evaluated",
            "objective_effect": "requires_separate_reviewed_condition_specific_evidence",
            "fuel_or_fire_claim": "requires_distinct_trait_and_measurement_context",
            "species_ranking": "refused",
            "planting_recommendation": "refused",
        },
    }


def refusal(request: ProfileRequest, code: str, message: str) -> dict[str, Any]:
    """Render a serving refusal while retaining the exact unanswered request."""
    return {**_empty_result(request), "state": "refused", "reason": {"code": code, "message": message}}


def invalid_request(message: str) -> dict[str, Any]:
    """Render invalid identity or pagination without claiming any profile was read."""
    return {
        "product": PRODUCT,
        "state": "refused",
        "reason": {"code": "invalid_profile_request", "message": message},
        "profile_release_id": None,
        "taxon_identity": None,
        "serving_source": "immutable_parquet",
        "sections": {name: {"state": "unavailable", "fields": []} for name in SECTION_NAMES},
    }


def _render_profile(request: ProfileRequest, release: PublishedRelease, profile: SpeciesProfile) -> dict[str, Any]:
    assertions = tuple(assertion for assertion in release.assertions if assertion.identity == request.identity)
    offset = _cursor_offset(request)
    if offset and offset >= len(assertions):
        raise ProfileRequestError("cursor lies outside this taxon's assertion evidence")
    page = assertions[offset : offset + request.assertion_limit]
    page_ids = {assertion.assertion_id for assertion in page}
    next_offset = offset + len(page)
    sections: dict[str, Any] = {}
    for section in profile.sections:
        fields = [
            {**field.model_dump(mode="json"), "evidence_complete": set(field.assertion_ids) <= page_ids}
            for field in section.fields
        ]
        sections[section.name] = {"state": "published", "fields": fields}
    result = _empty_result(request)
    result.update(
        state="published",
        reason=None,
        taxon=next(taxon.model_dump(mode="json") for taxon in release.taxa if taxon.identity == request.identity),
        sections=sections,
        release=release.manifest.model_dump(mode="json"),
        assertions=[assertion.model_dump(mode="json") for assertion in page],
        decisions=[
            decision.model_dump(mode="json") for decision in release.decisions if decision.identity == request.identity
        ],
        continuation={
            "assertion_limit": request.assertion_limit,
            "assertions_returned": len(page),
            "assertions_total": len(assertions),
            "next_cursor": f"{next_offset}.{_cursor_binding(request)}" if next_offset < len(assertions) else None,
            "evidence_complete": offset == 0 and next_offset == len(assertions),
        },
    )
    return result


def encode_profile_response(result: Mapping[str, Any]) -> bytes:
    """Encode the exact compact UTF-8 bytes shared by response budgeting and transports."""
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _bounded_response(request: ProfileRequest, result: dict[str, Any]) -> dict[str, Any]:
    if len(encode_profile_response(result)) > MAX_RESPONSE_BYTES:
        return refusal(
            request, "profile_response_over_budget", "The profile evidence exceeds the bounded response size."
        )
    return result


def _read_profile(request: ProfileRequest, storage: AvailabilityStorage | None) -> dict[str, Any]:
    if storage is None:
        try:
            storage = BotoAvailabilityStorage.from_settings()
        except ValueError:
            return refusal(
                request, "profile_store_unconfigured", "The botanical profile object store is not configured."
            )
    try:
        release = read_release(storage, request.release_id)
    except ProfileUnavailableError:
        return refusal(request, "profile_release_unpublished", "The exact requested profile release is not published.")
    except (ProfileError, AvailabilityMalformedError):
        return refusal(
            request, "profile_release_integrity", "The pinned profile release failed its integrity contract."
        )
    except (AvailabilityUnavailableError, BotoCoreError, ClientError, OSError):
        return refusal(request, "profile_store_unavailable", "The profile store could not complete the bounded read.")
    profile = release.lookup(request.identity)
    if profile is None:
        result = {
            **_empty_result(request),
            "state": "unknown",
            "reason": {
                "code": "taxon_unknown_in_release",
                "message": "This exact canonical taxon has no profile in the requested release.",
            },
            "release": release.manifest.model_dump(mode="json"),
        }
    else:
        result = _render_profile(request, release, profile)
    return _bounded_response(request, result)


async def read_species_profile(
    request: ProfileRequest, *, storage: AvailabilityStorage | None = None
) -> dict[str, Any]:
    """Read one profile and evidence page through shared process-wide serving admission."""
    try:
        async with asyncio.timeout(READ_TIMEOUT_SECONDS):
            return await run_bounded_read(lambda: _read_profile(request, storage), operation=PRODUCT)
    except TimeoutError:
        return refusal(request, "profile_read_timed_out", "The bounded botanical profile read timed out.")
    except ServingRefusalError:
        return refusal(request, "profile_read_at_capacity", "The shared serving reader is at capacity.")
