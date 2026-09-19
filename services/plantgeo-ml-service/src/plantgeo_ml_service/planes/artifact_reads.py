"""`artifacts/<kind>`: the bounded listing of what this service has trained, with digests and windows.

Layer L4. Why an undecodable artifact refuses instead of being skipped, and why the listing is
capped rather than paged, live in `AGENTS.md` in this directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import structlog

from plantgeo_ml_service.foundation.canonical import sha256_digest
from plantgeo_ml_service.pipeline.object_store import ObjectTooLargeError
from plantgeo_ml_service.planes import refusals
from plantgeo_ml_service.planes.wire import ARTIFACT_ABSENT_NO_ARTIFACT, ClaimProvenance

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from plantgeo_ml_service.pipeline.object_store import ListedObject, ReadOnlyObjectStore

logger = structlog.get_logger()

#: The artifact families this service writes, spelled as they appear in the object key and in the
#: `<kind>` path segment. HYPHENATED like every platform slug, amended 2026-09-19: the vocabulary
#: previously mixed separators (`analog_ensemble` beside `fire-risk`), which is a shape a client
#: has to guess at. Nothing is published to the real prefix, so no object was migrated.
KNOWN_ARTIFACT_KINDS: Final[tuple[str, ...]] = ("analog-ensemble", "fire-risk")

ARTIFACT_SEGMENT: Final = "artifacts"
ARTIFACT_SUFFIX: Final = ".json"

#: How many artifacts one listing FETCHES and answers with. The listing is newest-first, so the cap
#: keeps the answer on the artifacts a caller asked this question to see, and `listing_truncated`
#: says when older ones exist. Deliberately small: each one is a separate GET, and 200 serial GETs
#: is a minute of one serving slot spent on a question whose useful answer is the newest handful.
MAX_LISTED_ARTIFACTS: Final = 25

#: How large one artifact document may be before the read refuses. The fire-risk artifact is a few
#: hundred coefficients; anything near this ceiling is not one of ours.
MAX_ARTIFACT_BYTES: Final = 2 * 1024 * 1024

#: How many bytes ONE listing request may fetch in total. A per-object ceiling bounds each GET and
#: says nothing about their sum, which is what the request actually costs.
MAX_LISTING_BYTES: Final = 8 * 1024 * 1024

#: How many keys the listing WALKS before answering. It reads the whole population to sort it by
#: age; MAX_LISTED_ARTIFACTS then bounds how many of them are fetched.
MAX_WALKED_ARTIFACT_KEYS: Final = 5_000

#: The instant a listed object with no recorded modification time sorts as. A backend that reports
#: no time then falls back to the key order, which is the digest and is at least stable.
UNKNOWN_MODIFICATION_TIME: Final = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ArtifactSummary:
    """One stored artifact: what it is, what it was trained on, and whether its digest holds."""

    key: str
    sha256: str
    #: Whether the document's bytes still digest to the key that names them. A stored artifact whose
    #: digest moved is reported as such rather than dropped: silence would read as "never trained".
    digest_matches_key: bool
    schema_version: int | None
    feature_set_version: str | None
    trained_on: dict[str, str] | None
    cleared_strata: tuple[str, ...]

    def to_wire(self) -> dict[str, object]:
        """Render one artifact row."""
        return {
            "key": self.key,
            "sha256": self.sha256,
            "digest_matches_key": self.digest_matches_key,
            "schema_version": self.schema_version,
            "feature_set_version": self.feature_set_version,
            "trained_on": self.trained_on,
            "cleared_strata": list(self.cleared_strata),
        }


@dataclass(frozen=True, slots=True)
class ArtifactListing:
    """One bounded page of a model kind's artifacts, NEWEST FIRST, and whether older ones exist."""

    model_kind: str
    artifacts: tuple[ArtifactSummary, ...]
    claim: ClaimProvenance
    #: Whether the kind holds more artifacts than this answer carries. A typed field rather than a
    #: silence: an answer of 25 that might be 25 of 25 or 25 of 900 is not a count.
    listing_truncated: bool

    def to_wire(self) -> dict[str, object]:
        """Render the listing payload; the claim block is added by the route."""
        return {
            "model_kind": self.model_kind,
            "count": len(self.artifacts),
            "artifacts": [artifact.to_wire() for artifact in self.artifacts],
            "listing_truncated": self.listing_truncated,
        }


def list_artifacts(store: ReadOnlyObjectStore, *, model_kind: str, ml_prefix: str = "ml/") -> ArtifactListing:
    """Return one model kind's newest artifacts, bounded in count and in bytes, with their digests."""
    if model_kind not in KNOWN_ARTIFACT_KINDS:
        raise refusals.artifact_kind_unknown(kind=model_kind, known=KNOWN_ARTIFACT_KINDS)
    prefix = f"{ml_prefix}{ARTIFACT_SEGMENT}/{model_kind}/"
    listed = [
        entry
        for entry in store.list_recent_objects(prefix, max_keys=MAX_WALKED_ARTIFACT_KEYS)
        if entry.key.endswith(ARTIFACT_SUFFIX)
    ]
    selected = sorted(listed, key=_age_key, reverse=True)[:MAX_LISTED_ARTIFACTS]
    artifacts = _fetched(store, selected, model_kind=model_kind)
    return ArtifactListing(
        model_kind=model_kind,
        artifacts=artifacts,
        claim=_claim(artifacts),
        listing_truncated=len(listed) > len(selected),
    )


def _age_key(entry: ListedObject) -> tuple[datetime, str]:
    """Return one listed object's sort key: when the store wrote it, then its key as a tie-break."""
    return (entry.last_modified or UNKNOWN_MODIFICATION_TIME, entry.key)


def _fetched(
    store: ReadOnlyObjectStore, listed: Sequence[ListedObject], *, model_kind: str
) -> tuple[ArtifactSummary, ...]:
    """Fetch the selected artifacts in order, refusing once the request's byte budget is spent."""
    summaries: list[ArtifactSummary] = []
    budget = MAX_LISTING_BYTES
    for entry in listed:
        summary, spent = _summary(store, entry.key, model_kind=model_kind)
        budget -= spent
        if budget < 0:
            raise refusals.read_over_budget(
                operation="artifacts",
                detail=f"these artifacts pass the {MAX_LISTING_BYTES}-byte budget one listing may fetch",
            )
        summaries.append(summary)
    return tuple(summaries)


def _summary(store: ReadOnlyObjectStore, key: str, *, model_kind: str) -> tuple[ArtifactSummary, int]:
    """Read one artifact document, refusing rather than reporting an unreadable one as absent."""
    try:
        payload = store.read_object(key, max_bytes=MAX_ARTIFACT_BYTES)
    except ObjectTooLargeError as error:
        logger.error("machine_learning_artifact_over_budget", model_kind=model_kind, key=key)
        raise refusals.read_over_budget(
            operation="artifacts", detail=f"one stored artifact is over the {MAX_ARTIFACT_BYTES}-byte ceiling"
        ) from error
    if payload is None:
        raise _unreadable(model_kind, key, "the listed object is no longer there")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise _unreadable(model_kind, key, "the bytes are not decodable JSON") from error
    if not isinstance(document, dict):
        raise _unreadable(model_kind, key, "the document is not a JSON object")
    named_digest = key.rsplit("/", 1)[-1].removesuffix(ARTIFACT_SUFFIX)
    stored_digest = document.get("sha256")
    summary = ArtifactSummary(
        key=key,
        sha256=str(stored_digest) if isinstance(stored_digest, str) else sha256_digest(payload.decode("utf-8")),
        digest_matches_key=isinstance(stored_digest, str) and stored_digest == named_digest,
        schema_version=_whole_number(document, "schema_version"),
        feature_set_version=_text(document, "feature_set_version"),
        trained_on=_trained_on(document),
        cleared_strata=_cleared_strata(document),
    )
    return summary, len(payload)


def _unreadable(model_kind: str, key: str, detail: str) -> refusals.MachineLearningRefusalError:
    """Log WHICH artifact refused and return the refusal that names only what went wrong."""
    logger.error("machine_learning_artifact_unreadable", model_kind=model_kind, key=key, detail=detail)
    return refusals.artifact_unreadable(kind=model_kind, detail=detail)


def _trained_on(document: Mapping[str, Any]) -> dict[str, str] | None:
    """Return the artifact's training window, or `None` when it declares none."""
    first_day = _text(document, "trained_on_first_day")
    last_day = _text(document, "trained_on_last_day")
    if first_day is None or last_day is None:
        return None
    return {"first_day": first_day, "last_day": last_day}


def _cleared_strata(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the strata the artifact's backtest reference claims, which may be none."""
    backtest = document.get("backtest")
    if not isinstance(backtest, dict):
        return ()
    cleared = backtest.get("cleared_strata")
    if not isinstance(cleared, list):
        return ()
    return tuple(str(name) for name in cleared)


def _text(document: Mapping[str, Any], field: str) -> str | None:
    """Read one optional string field, treating any other shape as absent."""
    value = document.get(field)
    return value if isinstance(value, str) else None


def _whole_number(document: Mapping[str, Any], field: str) -> int | None:
    """Read one optional integer field, treating any other shape as absent."""
    value = document.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _claim(artifacts: tuple[ArtifactSummary, ...]) -> ClaimProvenance:
    """Return the claim block: the newest digest this kind holds, or the declared absence of one.

    The listing is newest-FIRST, so the newest artifact is the first row rather than the last one.
    """
    if not artifacts:
        return ClaimProvenance(artifact_sha256=None, artifact_absent_reason=ARTIFACT_ABSENT_NO_ARTIFACT, issued_on=None)
    return ClaimProvenance(artifact_sha256=artifacts[0].sha256, artifact_absent_reason=None, issued_on=None)


__all__ = [
    "KNOWN_ARTIFACT_KINDS",
    "MAX_ARTIFACT_BYTES",
    "MAX_LISTED_ARTIFACTS",
    "MAX_LISTING_BYTES",
    "ArtifactListing",
    "ArtifactSummary",
    "list_artifacts",
]
