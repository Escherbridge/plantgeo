"""Guarded, resumable bucket staging of one prepared shard and of the release index; see `AGENTS.md`, "Stage".

Staging never re-prepares (each local blob is checked against its manifest SHA by `read_blob`),
writes the shard manifest last, journals every object it wrote so a killed run resumes, and never
touches a day partition, the availability index or an admission pin.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.soil_survey.receipts import (
    AreaInventory,
    Blob,
    SoilSurveyError,
    digest,
    encoded,
    verify_blob,
)
from agri_data_service.foundation.soil_survey.release import (
    MAX_PREPARATION_SECONDS,
    MAX_RELEASE_BYTES,
    Release,
    ShardRef,
    manifest_key,
    release_key,
)
from agri_data_service.pipeline.direct.soil_survey.capture import read_blob
from agri_data_service.pipeline.direct.soil_survey.prepare import load_candidate_manifest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from typing import TextIO

    from agri_data_service.foundation.soil_survey.release import Candidate
    from agri_data_service.pipeline.parquet.availability_storage import AvailabilityStorage

#: The second, explicit operator switch `stage --apply` and `release --apply` require (F11).
STAGE_ALLOWED_VARIABLE: Final = "SSURGO_STAGE_ALLOWED"
DEFAULT_UPLOAD_BUDGET_SECONDS: Final = 600
_OBJECT_CONTENT_TYPE: Final = "application/octet-stream"
_JSON_CONTENT_TYPE: Final = "application/json"
#: Mirrors `prepare.py::_SHA256_PATTERN`; a manifest SHA reaches `journal_path` before
#: `load_candidate_manifest` has had a chance to validate it (`build_release` reads the journal
#: first), so the path built from it needs its own check.
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")


def require_stage_authorised(bucket: str, *, configured_bucket: str | None, environ: Mapping[str, str]) -> None:
    """Refuse a bucket write unless `--bucket` names the configured bucket and the switch is set."""
    if environ.get(STAGE_ALLOWED_VARIABLE) != "1":
        raise SoilSurveyError(f"bucket writes need {STAGE_ALLOWED_VARIABLE}=1 in the environment")
    if configured_bucket is None or bucket != configured_bucket:
        raise SoilSurveyError(
            f"--bucket {bucket!r} does not equal the configured OBJECT_STORE_BUCKET {configured_bucket!r}"
        )


@dataclass(frozen=True, slots=True)
class StageObject:
    """One content-addressed local blob and the bucket key it stages to."""

    key: str
    blob: Blob


@dataclass(frozen=True, slots=True)
class StagePlan:
    """Everything one `stage` invocation would write, manifest last."""

    manifest_sha256: str
    manifest_key: str
    manifest_bytes: int
    objects: tuple[StageObject, ...]
    include_source_evidence: bool

    @property
    def object_count(self) -> int:
        return len(self.objects) + 1

    @property
    def byte_count(self) -> int:
        return sum(item.blob.byte_count for item in self.objects) + self.manifest_bytes


@dataclass(frozen=True, slots=True)
class JournalState:
    """What one bucket's stage journal already records for one shard manifest."""

    staged_keys: frozenset[str]
    manifest_staged: bool
    source_evidence_staged: bool


def _source_evidence(candidate: Candidate) -> list[Blob]:
    blobs: list[Blob] = []
    for area in candidate.areas:
        blobs.extend(summary.response for summary in (area.opening, area.closing) if summary is not None)
        for page in area.pages:
            blobs.extend((page.keys_response, page.response))
    return blobs


def stage_plan(root: Path, manifest_sha256: str, *, include_source_evidence: bool) -> StagePlan:
    """Plan one shard's upload from local files alone; raw SDA evidence only when asked (owner Q2)."""
    candidate, payload = load_candidate_manifest(root, manifest_sha256)
    blobs = [part.blob for part in candidate.parts]
    if include_source_evidence:
        blobs.extend(_source_evidence(candidate))
    unique = {blob.sha256: blob for blob in blobs}
    return StagePlan(
        manifest_sha256=manifest_sha256,
        manifest_key=manifest_key(manifest_sha256),
        manifest_bytes=len(payload),
        objects=tuple(StageObject(key=blob.key, blob=blob) for blob in unique.values()),
        include_source_evidence=include_source_evidence,
    )


def journal_path(root: Path, manifest_sha256: str) -> Path:
    """One journal per shard manifest; every line names the bucket and prefix it describes."""
    if _SHA256_PATTERN.fullmatch(manifest_sha256) is None:
        raise SoilSurveyError("a stage journal is named by a lowercase 64-hex SHA-256")
    return root / f"stage-{manifest_sha256}.jsonl"


def read_journal(root: Path, manifest_sha256: str, *, bucket: str, prefix: str = "") -> JournalState:
    """Parse the journal, ignoring other buckets/prefixes and any torn line (a re-put is idempotent).

    A line missing "prefix" (written before this field existed) matches only the default `""`
    prefix, so a bucket recorded under one `OBJECT_STORE_PREFIX` is never mistaken for staged
    under a different one.
    """
    path = journal_path(root, manifest_sha256)
    staged: set[str] = set()
    manifest_staged = False
    evidence_staged = False
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            not isinstance(entry, dict)
            or entry.get("bucket") != bucket
            or entry.get("prefix", "") != prefix
            or not isinstance(entry.get("key"), str)
        ):
            continue
        staged.add(entry["key"])
        if entry.get("manifest") is True and entry["key"] == manifest_key(manifest_sha256):
            manifest_staged = True
            evidence_staged = evidence_staged or entry.get("include_source_evidence") is True
    return JournalState(frozenset(staged), manifest_staged, evidence_staged)


def _append(journal: TextIO, entry: Mapping[str, object]) -> None:
    journal.write(json.dumps(entry, sort_keys=True) + "\n")
    journal.flush()
    os.fsync(journal.fileno())


@dataclass(frozen=True, slots=True)
class StageReport:
    """One `stage --apply` outcome."""

    manifest_key: str
    manifest_sha256: str
    uploaded_objects: int
    resumed_objects: int
    uploaded_bytes: int


def _staged_in_bucket(storage: AvailabilityStorage, item: StageObject) -> bool:
    """True only when `item.key` is actually present in the bucket with its journaled bytes.

    The journal on its own only proves a `put_immutable` call once succeeded; it says nothing
    about the object now, after a bucket recreation, a lifecycle deletion, or an `OBJECT_STORE_PREFIX`
    change (guarded separately, by `prefix` in the journal entry). So a "resumed" item is re-read
    and re-verified against its blob, not merely trusted, before its upload is skipped.
    """
    stored = storage.read(item.key, max_bytes=item.blob.byte_count)
    if stored is None:
        return False
    try:
        verify_blob(item.blob, stored.payload)
    except SoilSurveyError:
        return False
    return True


def publish_candidate(  # noqa: PLR0913 - one parameter per explicit operator choice
    root: Path,
    manifest_sha256: str,
    storage: AvailabilityStorage,
    *,
    bucket: str,
    prefix: str = "",
    include_source_evidence: bool = False,
    upload_budget_seconds: float = DEFAULT_UPLOAD_BUDGET_SECONDS,
) -> StageReport:
    """Upload one shard's objects, resuming from the journal, then its manifest last with a readback."""
    if not 1 <= upload_budget_seconds <= MAX_PREPARATION_SECONDS:
        raise SoilSurveyError(f"staging needs an upload budget of 1..{MAX_PREPARATION_SECONDS} seconds")
    deadline = time.monotonic() + upload_budget_seconds
    plan = stage_plan(root, manifest_sha256, include_source_evidence=include_source_evidence)
    journaled = read_journal(root, manifest_sha256, bucket=bucket, prefix=prefix).staged_keys
    uploaded = 0
    uploaded_bytes = 0
    with journal_path(root, manifest_sha256).open("a", encoding="utf-8") as journal:
        for item in plan.objects:
            if item.key in journaled and _staged_in_bucket(storage, item):
                continue
            if time.monotonic() >= deadline:
                raise SoilSurveyError("upload budget reached before the manifest; re-run stage to resume")
            storage.put_immutable(item.key, read_blob(root, item.blob), content_type=_OBJECT_CONTENT_TYPE)
            _append(journal, {"bucket": bucket, "prefix": prefix, "key": item.key, "sha256": item.blob.sha256})
            uploaded += 1
            uploaded_bytes += item.blob.byte_count
        if time.monotonic() >= deadline:
            raise SoilSurveyError("upload budget reached before the manifest; re-run stage to resume")
        _, payload = load_candidate_manifest(root, manifest_sha256)
        storage.put_immutable(plan.manifest_key, payload, content_type=_JSON_CONTENT_TYPE)
        stored = storage.read(plan.manifest_key, max_bytes=len(payload))
        if stored is None or stored.payload != payload:
            raise SoilSurveyError("candidate manifest failed publication readback")
        _append(
            journal,
            {
                "bucket": bucket,
                "prefix": prefix,
                "key": plan.manifest_key,
                "sha256": manifest_sha256,
                "manifest": True,
                "include_source_evidence": include_source_evidence,
            },
        )
    return StageReport(
        manifest_key=plan.manifest_key,
        manifest_sha256=manifest_sha256,
        uploaded_objects=uploaded,
        resumed_objects=len(plan.objects) - uploaded,
        uploaded_bytes=uploaded_bytes,
    )


def build_release(
    root: Path, shard_manifests: Sequence[str], *, bucket: str, prefix: str = ""
) -> tuple[Release, bytes]:
    """Assemble the release index from staged shards and the root's `areas.json` scope census."""
    scope = AreaInventory.model_validate_json((root / "areas.json").read_bytes())
    shards: list[ShardRef] = []
    evidence_staged = True
    for sha in shard_manifests:
        journal = read_journal(root, sha, bucket=bucket, prefix=prefix)
        if not journal.manifest_staged:
            raise SoilSurveyError(
                f"shard manifest {sha} is not staged to {bucket!r} at prefix {prefix!r}; run stage first"
            )
        evidence_staged = evidence_staged and journal.source_evidence_staged
        candidate, payload = load_candidate_manifest(root, sha)
        shards.append(ShardRef.from_candidate(candidate, Blob(sha256=sha, byte_count=len(payload))))
    served = {area.area for shard in shards for area in shard.areas}
    try:
        release = Release(
            scope=scope,
            shards=tuple(sorted(shards, key=lambda shard: shard.shard)),
            pending_areas=tuple(sorted({entry.area for entry in scope.areas} - served)),
            source_evidence="staged" if evidence_staged else "capture_volume",
            release_day=max(shard.release_day for shard in shards),
            captured_at=max(shard.captured_at for shard in shards),
        )
    except ValueError as error:  # pydantic's ValidationError is a ValueError
        raise SoilSurveyError(f"shards do not form a release index: {error}") from error
    payload = encoded(release)
    if len(payload) > MAX_RELEASE_BYTES:
        raise SoilSurveyError(f"release index is {len(payload)} bytes, over the {MAX_RELEASE_BYTES}-byte cap")
    return release, payload


def publish_release(root: Path, release: Release, payload: bytes, storage: AvailabilityStorage) -> tuple[str, str]:
    """Check every shard manifest and part is actually in the bucket, then write the index once.

    Reading the journal back is not enough (`_staged_in_bucket`'s docstring): a release pins
    shards for serving, so every part byte a shard's manifest promises is re-read and re-verified
    here too, not only the manifest itself.
    """
    if encoded(release) != payload:
        raise SoilSurveyError("release payload is not the canonical encoding of its index")
    for shard in release.shards:
        stored = storage.read(manifest_key(shard.manifest.sha256), max_bytes=shard.manifest.byte_count)
        verify_blob(shard.manifest, None if stored is None else stored.payload)
        candidate, _ = load_candidate_manifest(root, shard.manifest.sha256)
        for part in candidate.parts:
            part_stored = storage.read(part.blob.key, max_bytes=part.blob.byte_count)
            verify_blob(part.blob, None if part_stored is None else part_stored.payload)
    sha = digest(payload)
    local = root / f"release-{sha}.json"
    if not local.exists():
        local.write_bytes(payload)
    key = release_key(sha)
    storage.put_immutable(key, payload, content_type=_JSON_CONTENT_TYPE)
    stored = storage.read(key, max_bytes=len(payload))
    if stored is None or stored.payload != payload:
        raise SoilSurveyError("release index failed publication readback")
    return key, sha


__all__ = [
    "DEFAULT_UPLOAD_BUDGET_SECONDS",
    "STAGE_ALLOWED_VARIABLE",
    "JournalState",
    "StageObject",
    "StagePlan",
    "StageReport",
    "build_release",
    "journal_path",
    "publish_candidate",
    "publish_release",
    "read_journal",
    "require_stage_authorised",
    "stage_plan",
]
