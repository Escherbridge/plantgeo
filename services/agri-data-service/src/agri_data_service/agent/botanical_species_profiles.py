"""The canonical species-information tool; see `AGENTS.md` for evidence and refusal rules."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Annotated, Any

from anthropic import beta_async_tool
from pydantic import Field

from agri_data_service.planes.botanical_species_profiles import (
    DEFAULT_ASSERTION_LIMIT,
    MAX_ASSERTION_LIMIT,
    MAX_CURSOR_LENGTH,
    ProfileRequestError,
    encode_profile_response,
    invalid_request,
    parse_profile_request,
    read_species_profile,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage

type _Recorder = Callable[[str, int, dict[str, Any]], None]
_storage: ContextVar[AvailabilityStorage | None] = ContextVar("botanical_profile_storage", default=None)
_recorder: ContextVar[_Recorder | None] = ContextVar("botanical_profile_recorder", default=None)

TaxonId = Annotated[
    str, Field(min_length=1, max_length=200, description="Canonical accepted taxon concept ID; never a name.")
]
Authority = Annotated[str, Field(min_length=1, max_length=128, description="The exact canonical taxonomic authority.")]
AuthorityVersion = Annotated[
    str, Field(min_length=1, max_length=128, description="The exact version of that authority.")
]
PinnedRelease = Annotated[
    str,
    Field(
        pattern=r"^bspf-[0-9a-f]{64}$", description="Exact immutable botanical profile release ID; latest is refused."
    ),
]
AssertionLimit = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_ASSERTION_LIMIT,
        description="Maximum raw assertions in this page; follow continuation for evidence.",
    ),
]
ContinuationCursor = Annotated[
    str | None,
    Field(
        max_length=MAX_CURSOR_LENGTH, description="A returned cursor bound to this exact release and taxon, or null."
    ),
]


@contextmanager
def use_profile_storage(storage: AvailabilityStorage) -> Iterator[None]:
    """Inject a local immutable store for a bounded agent run."""
    token = _storage.set(storage)
    try:
        yield
    finally:
        _storage.reset(token)


@contextmanager
def record_profile_tools(recorder: _Recorder) -> Iterator[None]:
    """Bind the agent ledger without treating reference profiles as location observations."""
    token = _recorder.set(recorder)
    try:
        yield
    finally:
        _recorder.reset(token)


@beta_async_tool
async def species_information(  # noqa: PLR0913 - all six arguments belong to the bounded model-facing schema.
    taxon_id: TaxonId,
    authority: Authority,
    authority_version: AuthorityVersion,
    release_id: PinnedRelease,
    assertion_limit: AssertionLimit = DEFAULT_ASSERTION_LIMIT,
    cursor: ContinuationCursor = None,
) -> str:
    """Read one pinned species profile, distinct trait sections, raw citations and explicit unknown/refusal states."""
    parameters = {
        "taxon_id": taxon_id,
        "authority": authority,
        "authority_version": authority_version,
        "release_id": release_id,
        "assertion_limit": str(assertion_limit),
    }
    if cursor is not None:
        parameters["cursor"] = cursor
    try:
        request = parse_profile_request(parameters)
        result = await read_species_profile(request, storage=_storage.get())
    except ProfileRequestError as error:
        result = invalid_request(str(error))
    recorder = _recorder.get()
    if recorder is not None:
        recorder(
            "species_information",
            0,
            {
                "profile_count": int(result["state"] == "published"),
                "state": result["state"],
                "profile_release_id": result["profile_release_id"],
                "evidence_domain": "botanical_reference",
            },
        )
    return encode_profile_response(result).decode("utf-8")
