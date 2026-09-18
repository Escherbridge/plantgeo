"""The checksum-bound `_LATEST.json` pointer for the botanical-occurrences lane (layer-lanes §4a).

Rationale: see `AGENTS.md` in this directory, "The current pointer".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from agri_data_service.foundation.canonical import sha256_digest

#: Bumped only when the document's field set changes; a reader refuses a version it does not know.
POINTER_SCHEMA_VERSION: Final = 1

#: §4a names the pointer `availability/_LATEST.json` under the lane root, and this lane keeps that
#: name even though its unit is a release set rather than a day: the read path is the same one
#: pointer GET plus one data GET, so it reads as the same artifact to an operator.
LATEST_POINTER_NAME: Final = "availability/_LATEST.json"

#: A pointer is a handful of short strings. Anything larger is not this document.
POINTER_MAX_BYTES: Final = 8 * 1024

SHA256_HEX_LENGTH: Final = 64


class BotanicalPointerMalformedError(ValueError):
    """The pointer bytes are not a document this lane wrote."""


class BotanicalLatestPointer(BaseModel):
    """One checksum-bound pointer: which generation is current, and the digest that proves it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pointer_schema_version: int = Field(ge=POINTER_SCHEMA_VERSION, le=POINTER_SCHEMA_VERSION)
    product: str = Field(min_length=1)
    generation_id: str = Field(min_length=1)
    manifest_key: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=rf"^[0-9a-f]{{{SHA256_HEX_LENGTH}}}$")
    published_at: str | None = None
    pointer_written_at: str


def manifest_digest(manifest_bytes: bytes) -> str:
    """Return the digest a pointer binds its generation's manifest by."""
    return sha256_digest(manifest_bytes)


def build_latest_pointer(
    *, product: str, generation_id: str, manifest_key: str, manifest_bytes: bytes, published_at: str | None
) -> BotanicalLatestPointer:
    """Bind one already-written manifest into the pointer document that will name it current."""
    return BotanicalLatestPointer(
        pointer_schema_version=POINTER_SCHEMA_VERSION,
        product=product,
        generation_id=generation_id,
        manifest_key=manifest_key,
        manifest_sha256=manifest_digest(manifest_bytes),
        published_at=published_at,
        pointer_written_at=datetime.now(UTC).isoformat(),
    )


def encode_latest_pointer(pointer: BotanicalLatestPointer) -> bytes:
    """Render the pointer as the exact bytes written to the object store."""
    return pointer.model_dump_json(indent=None).encode("utf-8")


def parse_latest_pointer(payload: bytes) -> BotanicalLatestPointer:
    """Validate pointer bytes, raising rather than returning a partially-trusted document.

    The size ceiling is checked before parsing: a reader that JSON-decodes an arbitrarily large
    object to discover it is not a pointer has already paid the cost the ceiling exists to refuse.
    """
    if len(payload) > POINTER_MAX_BYTES:
        raise BotanicalPointerMalformedError(f"the pointer document exceeds {POINTER_MAX_BYTES} bytes")
    try:
        return BotanicalLatestPointer.model_validate_json(payload)
    except ValueError as error:
        raise BotanicalPointerMalformedError(f"the pointer document is not valid: {error}") from error


__all__ = [
    "LATEST_POINTER_NAME",
    "POINTER_MAX_BYTES",
    "POINTER_SCHEMA_VERSION",
    "BotanicalLatestPointer",
    "BotanicalPointerMalformedError",
    "build_latest_pointer",
    "encode_latest_pointer",
    "manifest_digest",
    "parse_latest_pointer",
]
