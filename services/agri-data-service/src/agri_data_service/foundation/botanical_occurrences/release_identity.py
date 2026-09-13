"""Deterministic release and release-set identities, derived only from bytes and pinned recipes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Field separator inside a hashed tuple. A byte no identifier or version string may contain, so two
#: different tuples cannot hash to the same pre-image by moving the boundary between fields.
_FIELD_SEPARATOR: Final = "\x1f"


def _digest(*parts: str) -> str:
    """Return the sha256 hex of a separator-joined tuple."""
    return hashlib.sha256(_FIELD_SEPARATOR.join(parts).encode("utf-8")).hexdigest()


def release_key(collection_key: str, source_version: str, archive_sha256: str) -> str:
    """Identify ONE immutable release: which collection, which publisher version, which exact bytes.

    The archive hash is what actually makes it immutable. A filename, a version URL, an HTTP
    validator and a retrieval time all change without the bytes changing, and none of them is here.
    """
    if not collection_key or not archive_sha256:
        raise ValueError("a release key needs both a collection key and the archive's sha256")
    return _digest(collection_key, source_version, archive_sha256)


@dataclass(frozen=True, slots=True)
class ReleaseSetIdentity:
    """The identity of one published generation, and the four inputs that determine it."""

    release_set_id: str
    release_keys: tuple[str, ...]
    taxonomy_recipe_version: str
    qc_policy_version: str
    support_version: str


def release_set_id(
    release_keys: Iterable[str],
    *,
    taxonomy_recipe_version: str,
    qc_policy_version: str,
    support_version: str,
) -> str:
    """Identify a generation: the SET of releases it reads plus every recipe that shaped it.

    Sorted and de-duplicated, so the identity depends on which releases are in the set and not on the
    order a caller happened to list them. Changing any recipe changes the identity, which is what
    makes a rights, taxonomy or QC revision a NEW generation rather than an edit of a served one.
    """
    keys = tuple(sorted(set(release_keys)))
    if not keys:
        raise ValueError("a release set must name at least one release")
    return _digest(*keys, taxonomy_recipe_version, qc_policy_version, support_version)


def build_release_set_identity(
    release_keys: Iterable[str],
    *,
    taxonomy_recipe_version: str,
    qc_policy_version: str,
    support_version: str,
) -> ReleaseSetIdentity:
    """Return the identity object a publisher stamps a generation with."""
    keys = tuple(sorted(set(release_keys)))
    return ReleaseSetIdentity(
        release_set_id=release_set_id(
            keys,
            taxonomy_recipe_version=taxonomy_recipe_version,
            qc_policy_version=qc_policy_version,
            support_version=support_version,
        ),
        release_keys=keys,
        taxonomy_recipe_version=taxonomy_recipe_version,
        qc_policy_version=qc_policy_version,
        support_version=support_version,
    )


def occurrence_id(collection_key: str, source_record_key: str, row_sha256: str) -> str:
    """Identify one normalized occurrence: native key plus the exact content it was read from.

    Content is in the key on purpose. A publisher that reuses a native key for different content has
    published a different record, and this lane must be able to see that rather than overwrite it.
    """
    return _digest(collection_key, source_record_key, row_sha256)


def row_sha256(values: Iterable[str]) -> str:
    """Return the content hash of one source row, over its verbatim values in column order."""
    return _digest(*values)


__all__ = [
    "ReleaseSetIdentity",
    "build_release_set_identity",
    "occurrence_id",
    "release_key",
    "release_set_id",
    "row_sha256",
]
