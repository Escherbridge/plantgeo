"""Bucket sync: mirror `raw/`, `corpus/` and optionally a prebuilt index between the cache and the bucket.

Additive in both directions (nothing is ever deleted) and three-way: each key is compared with what it looked
like at the last pull or push (the local sync manifest), so a pull never overwrites an unpushed local change and
a push never overwrites a remote change made since; see AGENTS.md section "Storage".
"""

import hashlib
import io
import json
import logging
import re
import shutil
import tarfile
import tempfile
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Final, Literal

import boto3

from strategy_knowledge.config import Settings
from strategy_knowledge.corpus import (
    CHROMA_DIRECTORY,
    CORPUS_DIRECTORY,
    INDEX_DIRECTORY,
    RAW_DIRECTORY,
    SOURCES_FILE,
    STRATEGIES_DIRECTORY,
    CorpusStore,
    write_json,
)
from strategy_knowledge.index import open_client, read_index_stamp

logger = logging.getLogger(__name__)

SYNCED_DIRECTORIES: Final = (RAW_DIRECTORY, CORPUS_DIRECTORY)
INDEX_ARCHIVE_NAME: Final = "chroma.tar.gz"
INDEX_POINTER_NAME: Final = "LATEST"
INDEX_POINTER_KEY: Final = f"{INDEX_DIRECTORY}/{INDEX_POINTER_NAME}"
CONTENT_TYPES: Final = {".json": "application/json", ".txt": "text/plain; charset=utf-8", ".gz": "application/gzip"}
CORPUS_VERSION_FORMAT: Final = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_SCHEMA: Final = 1
#: Recorded instead of a real ETag/MD5 so the next sync in the other direction sees that side as changed.
FORCE_CHANGED: Final = ""
#: How many refused archive member names a log line shows.
REPORTED_MEMBERS: Final = 5

#: How a key that changed on both sides since the last sync is settled: report it, or let one side win.
ConflictPolicy = Literal["fail", "local", "remote"]


def conflict_policy(*, force_local: bool, force_remote: bool) -> ConflictPolicy:
    """The policy the `--force-local` / `--force-remote` flags select (neither: report conflicts and fail)."""
    if force_local:
        return "local"
    return "remote" if force_remote else "fail"


class KeyState(StrEnum):
    """One key's local file against its remote object and the last-synced record of both."""

    SAME = "same"
    LOCAL_ONLY = "local_only"
    REMOTE_ONLY = "remote_only"
    LOCAL_CHANGED = "local_changed"
    REMOTE_CHANGED = "remote_changed"
    CONFLICT = "conflict"


@dataclass
class SyncReport:
    """What a pull or push did, skipped, could not decide, or refused."""

    transferred: list[str] = field(default_factory=list)
    unchanged: int = 0
    kept_local_changes: list[str] = field(default_factory=list)
    kept_remote_changes: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    resolved_conflicts: dict[str, str] = field(default_factory=dict)
    rejected_keys: list[str] = field(default_factory=list)
    index_refusals: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        """True when a conflict was left undecided, a remote key was refused, or a requested index push was not."""
        return bool(self.conflicts or self.rejected_keys or self.index_refusals)

    def as_response(self) -> dict[str, Any]:
        """The JSON the CLI prints."""
        return {
            "transferred": self.transferred,
            "unchanged": self.unchanged,
            "kept_local_changes": self.kept_local_changes,
            "kept_remote_changes": self.kept_remote_changes,
            "conflicts": self.conflicts,
            "resolved_conflicts": self.resolved_conflicts,
            "rejected_keys": self.rejected_keys,
            "index_refusals": self.index_refusals,
        }


def md5_file(path: Path) -> str:
    """Hex MD5 of a file: what a single-part S3 PUT reports as the ETag."""
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def md5_bytes(payload: bytes) -> str:
    """Hex MD5 of a byte string."""
    return hashlib.md5(payload, usedforsecurity=False).hexdigest()


#: (local changed since the last sync, remote changed since it) -> state. Neither changed but not byte-equal by
#: ETag means a multipart upload, whose ETag is not an MD5: the two were in step at the last sync.
CHANGE_STATES: Final = {
    (True, True): KeyState.CONFLICT,
    (True, False): KeyState.LOCAL_CHANGED,
    (False, True): KeyState.REMOTE_CHANGED,
    (False, False): KeyState.SAME,
}


@dataclass(frozen=True, slots=True)
class KeyView:
    """One key as a sync sees it: its local MD5, its remote ETag, where it lives locally, and its state."""

    relative: str
    local_md5: str | None
    remote_etag: str | None
    target: Path
    state: KeyState


@dataclass(frozen=True, slots=True)
class IndexPointer:
    """The index/LATEST a push would write: the index's own corpus_version, the payload and its three-way state."""

    corpus_version: str
    payload: bytes
    local_md5: str
    remote_etag: str | None
    state: KeyState


def classify(local_md5: str | None, remote_etag: str | None, base: dict[str, str] | None) -> KeyState:
    """Three-way comparison of a key: identical, one-sided, changed on one side since the last sync, or both."""
    if local_md5 is not None and local_md5 == remote_etag:
        return KeyState.SAME
    if local_md5 is None or remote_etag is None:
        return KeyState.REMOTE_ONLY if local_md5 is None else KeyState.LOCAL_ONLY
    local_changed = base is None or base.get("md5") != local_md5
    remote_changed = base is None or base.get("etag") != remote_etag
    return CHANGE_STATES[local_changed, remote_changed]


def publish_rank(relative: str) -> tuple[int, str]:
    """Transfer order: raw text, then plans and findings, then the strategy registry, and sources.json last.

    A transfer that stops part-way then never leaves a sources.json or registry naming a file that did not arrive;
    a raw file or plan the registry does not list yet is inert (`index` excludes unregistered sources).
    """
    if relative == SOURCES_FILE:
        return 3, relative
    if relative.startswith(f"{STRATEGIES_DIRECTORY}/"):
        return 2, relative
    return (0 if relative.startswith(f"{RAW_DIRECTORY}/") else 1), relative


def is_index_member(member: tarfile.TarInfo) -> bool:
    """A regular file or directory under chroma/, named by a relative path with no `..`, backslash or drive."""
    name = member.name
    if not (member.isfile() or member.isdir()) or "\\" in name or PureWindowsPath(name).drive:
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and path.parts[:1] == (CHROMA_DIRECTORY,)


def swap_directory(replacement: Path, target: Path) -> None:
    """Put `replacement` at `target` with two renames, restoring the previous directory if the second one fails."""
    retired = target.with_name(f".{target.name}-retired-{uuid.uuid4().hex[:8]}")
    had_previous = target.exists()
    if had_previous:
        target.rename(retired)
    try:
        replacement.rename(target)
    except OSError:
        if had_previous:
            retired.rename(target)
        raise
    if had_previous:
        shutil.rmtree(retired, ignore_errors=True)


class BucketSync:
    """One boto3 client, one bucket, one prefix; constructing it performs no network call."""

    def __init__(self, settings: Settings, store: CorpusStore, client: Any | None = None) -> None:
        self.store = store
        self.prefix = settings.prefix
        if client is None:
            object_store = settings.object_store()
            client = boto3.client(
                "s3",
                endpoint_url=object_store.endpoint_url,
                region_name=object_store.region,
                aws_access_key_id=object_store.access_key_id.get_secret_value(),
                aws_secret_access_key=object_store.secret_access_key.get_secret_value(),
            )
            self.bucket = object_store.bucket
        else:
            self.bucket = settings.object_store_values.get("OBJECT_STORE_BUCKET", "")
        self.client = client

    def _remote_etags(self, relative_prefix: str) -> dict[str, str]:
        """Prefix-relative key -> ETag (quotes stripped) for every object under a directory."""
        etags: dict[str, str] = {}
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=f"{self.prefix}{relative_prefix}/"):
            for item in page.get("Contents", []):
                etags[item["Key"].removeprefix(self.prefix)] = str(item.get("ETag", "")).strip('"')
        return etags

    def _local_files(self, relative_prefix: str) -> Iterator[tuple[str, Path]]:
        """(prefix-relative key, path) for every local file under a directory."""
        root = self.store.root / relative_prefix
        if not root.is_dir():
            return
        for path in sorted(root.rglob("*")):
            if path.is_file():
                yield path.relative_to(self.store.root).as_posix(), path

    def local_target(self, relative: str) -> Path | None:
        """Where a remote key lands locally, or None for a key that is a directory marker or escapes the cache."""
        if not relative or relative.endswith("/") or relative.split("/", 1)[0] not in SYNCED_DIRECTORIES:
            return None
        target = self.store.root / relative
        if not target.resolve().is_relative_to(self.store.root.resolve()):
            return None
        return target

    def _load_manifest(self) -> dict[str, dict[str, str]]:
        path = self.store.sync_manifest_path
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload.get("keys") or {}) if isinstance(payload, dict) else {}

    def _save_manifest(self, manifest: dict[str, dict[str, str]]) -> None:
        write_json(self.store.sync_manifest_path, {"schema": MANIFEST_SCHEMA, "keys": dict(sorted(manifest.items()))})

    def _keys(self, directory: str, manifest: dict[str, dict[str, str]], report: SyncReport) -> Iterator[KeyView]:
        """Every key on either side of one directory, classified; unsafe remote keys are refused, not yielded."""
        remote = self._remote_etags(directory)
        local = dict(self._local_files(directory))
        for relative in sorted(set(remote) | set(local)):
            target = local.get(relative) or self.local_target(relative)
            if target is None:
                report.rejected_keys.append(relative)
                logger.error("refused remote key %r: a directory marker or a path outside the cache", relative)
                continue
            local_md5 = md5_file(target) if target.is_file() else None
            remote_etag = remote.get(relative)
            state = classify(local_md5, remote_etag, manifest.get(relative))
            yield KeyView(relative, local_md5, remote_etag, target, state)

    def _planned_keys(self, manifest: dict[str, dict[str, str]], report: SyncReport) -> list[KeyView]:
        """Every key of raw/ and corpus/, classified before anything moves, in `publish_rank` order."""
        keys = [key for directory in SYNCED_DIRECTORIES for key in self._keys(directory, manifest, report)]
        return sorted(keys, key=lambda key: publish_rank(key.relative))

    @staticmethod
    def _blocked_by_conflicts(
        states: Sequence[tuple[str, KeyState]],
        report: SyncReport,
        on_conflict: ConflictPolicy,
    ) -> bool:
        """With no side chosen, any conflict stops the whole transfer in that direction; every one is reported."""
        conflicts = [relative for relative, state in states if state is KeyState.CONFLICT]
        if not conflicts or on_conflict != "fail":
            return False
        report.conflicts.extend(conflicts)
        return True

    def pull(self, *, with_index: bool = False, on_conflict: ConflictPolicy = "fail") -> SyncReport:
        """Download remote changes under raw/ and corpus/, never over a local change made since the last sync.

        Every key is classified first: an undecided conflict anywhere stops the pull before anything is downloaded.
        """
        report = SyncReport()
        manifest = self._load_manifest()
        keys = self._planned_keys(manifest, report)
        if self._blocked_by_conflicts([(key.relative, key.state) for key in keys], report, on_conflict):
            return report
        try:
            for key in keys:
                self._pull_key(key, manifest, report, on_conflict)
            if with_index:
                self._pull_index(manifest, report)
        finally:
            self._save_manifest(manifest)
        return report

    def _pull_key(
        self,
        key: KeyView,
        manifest: dict[str, dict[str, str]],
        report: SyncReport,
        on_conflict: ConflictPolicy,
    ) -> None:
        """Apply one key's pull decision and record the synced state."""
        if key.state is KeyState.SAME and key.remote_etag is not None and key.local_md5 is not None:
            report.unchanged += 1
            manifest[key.relative] = {"etag": key.remote_etag, "md5": key.local_md5}
        elif key.state in (KeyState.LOCAL_ONLY, KeyState.LOCAL_CHANGED):
            report.kept_local_changes.append(key.relative)
        elif key.state is KeyState.CONFLICT and on_conflict == "fail":
            report.conflicts.append(key.relative)
        elif key.state is KeyState.CONFLICT and on_conflict == "local":
            # Record the remote as seen, so the next push treats the kept local file as the change to upload.
            report.resolved_conflicts[key.relative] = "local"
            manifest[key.relative] = {"etag": key.remote_etag or FORCE_CHANGED, "md5": FORCE_CHANGED}
        elif key.remote_etag is not None:
            if key.state is KeyState.CONFLICT:
                report.resolved_conflicts[key.relative] = "remote"
            key.target.parent.mkdir(parents=True, exist_ok=True)
            self.client.download_file(self.bucket, f"{self.prefix}{key.relative}", str(key.target))
            manifest[key.relative] = {"etag": key.remote_etag, "md5": md5_file(key.target)}
            report.transferred.append(key.relative)

    def push(
        self,
        *,
        with_index: bool = False,
        corpus_only: bool = False,
        on_conflict: ConflictPolicy = "fail",
    ) -> SyncReport:
        """Upload local raw/ and corpus/ changes (sources.json last); with `with_index`, publish the index too.

        An atomic publish (`with_index=True`) checks everything before the first upload — the local index's
        publishability against the local corpus, then every key's three-way conflict state — and uploads in a
        fixed order: the index archive, the corpus, and the LATEST pointer last, so a run that stops partway
        never leaves LATEST naming a corpus version whose files did not fully arrive. A bare push additionally
        refuses when the bucket already serves a published corpus version the local corpus would diverge from,
        unless `corpus_only` acknowledges that the server will refuse to serve until a matching index follows.
        """
        report = SyncReport()
        manifest = self._load_manifest()
        keys = self._planned_keys(manifest, report)
        pointer = self._index_pointer(manifest, report) if with_index else None
        if with_index and pointer is None:
            return report
        if not with_index and not corpus_only and self._refuse_divergent_corpus(report):
            return report
        # `report.rejected_keys` (an unsafe remote key under corpus/) also means the bucket's corpus cannot be
        # trusted to match what this push would leave; the old pointer guard checked this and the new one must
        # too (AGENTS.md "Storage").
        corpus_diverges = pointer is not None and (
            self._corpus_will_diverge(keys, on_conflict) or bool(report.rejected_keys)
        )
        states = [(key.relative, key.state) for key in keys]
        if pointer is not None:
            states.append((INDEX_POINTER_KEY, pointer.state))
        if self._blocked_by_conflicts(states, report, on_conflict):
            return report
        publish_archive = (
            pointer is not None and not corpus_diverges and self._pointer_needs_upload(pointer, on_conflict)
        )
        try:
            if publish_archive:
                self._push_archive(pointer, report)
            for key in keys:
                # A diverging corpus refuses the index below; local-changed corpus keys are skipped here too
                # (not just the index), so the push is atomic end to end instead of half-uploading the corpus
                # while only the index refuses (AGENTS.md "Storage").
                self._push_key(key, manifest, report, on_conflict, upload_local=not corpus_diverges)
            if pointer is not None:
                self._push_pointer(pointer, manifest, report, on_conflict, corpus_diverges=corpus_diverges)
        finally:
            self._save_manifest(manifest)
        return report

    def _remote_published_corpus_version(self) -> str | None:
        """The corpus_version index/LATEST names in the bucket, or None when nothing is published there yet."""
        if INDEX_POINTER_KEY not in self._remote_etags(INDEX_DIRECTORY):
            return None
        pointer = self.client.get_object(Bucket=self.bucket, Key=f"{self.prefix}{INDEX_POINTER_KEY}")
        return pointer["Body"].read().decode(errors="replace").strip()

    def _refuse_divergent_corpus(self, report: SyncReport) -> bool:
        """Refuse a bare push (no `--corpus-only`) when the bucket's published corpus would diverge from ours."""
        published = self._remote_published_corpus_version()
        local_version = self.store.corpus_version()
        if published is None or published == local_version:
            return False
        self._refuse_index(
            report,
            f"the bucket already serves corpus {published}, which differs from the local corpus {local_version}; "
            "pass --corpus-only to push anyway (the server will refuse to serve until a matching index is "
            "published) or `sync push --with-index` to publish both together",
        )
        return True

    @staticmethod
    def _corpus_will_diverge(keys: Sequence[KeyView], on_conflict: ConflictPolicy) -> bool:
        """True when this push will leave the bucket's corpus different from what the local index was built from."""
        return any(
            key.state in (KeyState.REMOTE_ONLY, KeyState.REMOTE_CHANGED)
            or (key.state is KeyState.CONFLICT and on_conflict == "remote")
            for key in keys
        )

    @staticmethod
    def _pointer_needs_upload(pointer: IndexPointer, on_conflict: ConflictPolicy) -> bool:
        """False when the pointer step below will short-circuit without writing (already published, or remote wins)."""
        if pointer.state is KeyState.SAME:
            return False
        if pointer.state is KeyState.CONFLICT and on_conflict == "remote":
            return False
        return pointer.state is not KeyState.REMOTE_CHANGED

    def _push_archive(self, pointer: IndexPointer, report: SyncReport) -> None:
        """Upload the index archive under its version-keyed path — first in the atomic-publish order."""
        archive_key = f"{INDEX_DIRECTORY}/{pointer.corpus_version}/{INDEX_ARCHIVE_NAME}"
        self._put(archive_key, self._index_archive())
        report.transferred.append(archive_key)

    def _push_key(
        self,
        key: KeyView,
        manifest: dict[str, dict[str, str]],
        report: SyncReport,
        on_conflict: ConflictPolicy,
        *,
        upload_local: bool = True,
    ) -> None:
        """Apply one key's push decision and record the synced state.

        `upload_local=False` (an atomic `--with-index` push whose corpus would diverge) still reports the
        remote-side outcomes as usual but skips the local-changed upload and its manifest write, so a local
        change is never partially uploaded while the index refuses beside it: the whole corpus is left exactly
        as it was, ready to retry once the divergence is resolved (AGENTS.md "Storage").
        """
        if key.state is KeyState.SAME and key.remote_etag is not None and key.local_md5 is not None:
            report.unchanged += 1
            manifest[key.relative] = {"etag": key.remote_etag, "md5": key.local_md5}
        elif key.state in (KeyState.REMOTE_ONLY, KeyState.REMOTE_CHANGED):
            report.kept_remote_changes.append(key.relative)
        elif key.state is KeyState.CONFLICT and on_conflict == "fail":
            report.conflicts.append(key.relative)
        elif key.state is KeyState.CONFLICT and on_conflict == "remote":
            # Record the local file as seen, so the next pull treats the kept remote object as the change.
            report.resolved_conflicts[key.relative] = "remote"
            manifest[key.relative] = {"etag": FORCE_CHANGED, "md5": key.local_md5 or FORCE_CHANGED}
        elif key.local_md5 is not None and upload_local:
            if key.state is KeyState.CONFLICT:
                report.resolved_conflicts[key.relative] = "local"
            etag = self._put(key.relative, key.target.read_bytes()) or key.local_md5
            manifest[key.relative] = {"etag": etag, "md5": key.local_md5}
            report.transferred.append(key.relative)

    def _put(self, relative: str, payload: bytes) -> str:
        """Single-part PUT, so the ETag stays the MD5 the next sync compares against; returns that ETag."""
        content_type = CONTENT_TYPES.get(Path(relative).suffix, "application/octet-stream")
        response = self.client.put_object(
            Bucket=self.bucket,
            Key=f"{self.prefix}{relative}",
            Body=payload,
            ContentType=content_type,
        )
        return str((response or {}).get("ETag", "")).strip('"')

    @staticmethod
    def _refuse_index(report: SyncReport, reason: str) -> None:
        """Record (and log) why a requested index push did not happen."""
        report.index_refusals.append(reason)
        logger.error("index not pushed: %s", reason)

    def _publishable_index_version(self, report: SyncReport) -> str | None:
        """The local index's own stamped corpus_version, when that index reflects the local corpus fully."""
        chroma = self.store.chroma_path
        if not chroma.is_dir():
            self._refuse_index(report, f"no local index at {chroma}")
            return None
        stamp = read_index_stamp(open_client(str(chroma)))
        reasons = stamp.stale_against(self.store.corpus_version())
        for reason in reasons:
            self._refuse_index(report, reason)
        return None if reasons else stamp.corpus_version

    def _index_pointer(self, manifest: dict[str, dict[str, str]], report: SyncReport) -> IndexPointer | None:
        """The index/LATEST a push would write and its three-way state; None (refusal recorded) if unpublishable."""
        version = self._publishable_index_version(report)
        if version is None:
            return None
        payload = f"{version}\n".encode()
        local_md5 = md5_bytes(payload)
        remote_etag = self._remote_etags(INDEX_DIRECTORY).get(INDEX_POINTER_KEY)
        state = classify(local_md5, remote_etag, manifest.get(INDEX_POINTER_KEY))
        return IndexPointer(version, payload, local_md5, remote_etag, state)

    def _push_pointer(
        self,
        pointer: IndexPointer,
        manifest: dict[str, dict[str, str]],
        report: SyncReport,
        on_conflict: ConflictPolicy,
        *,
        corpus_diverges: bool,
    ) -> None:
        """Write index/LATEST last, once the archive is up and the pointer's three-way check allows it.

        `corpus_diverges` is decided up front from the keys' own states (`_corpus_will_diverge`), so this refusal
        needs no re-read of the bucket: it is true exactly when the corpus push above left (or will leave) the
        bucket's corpus different from the local corpus the archive was built from.
        """
        if pointer.state is KeyState.SAME:
            manifest[INDEX_POINTER_KEY] = {"etag": pointer.remote_etag or pointer.local_md5, "md5": pointer.local_md5}
            report.unchanged += 1
            return
        if pointer.state is KeyState.CONFLICT and on_conflict == "remote":
            report.resolved_conflicts[INDEX_POINTER_KEY] = "remote"
            return
        if pointer.state is KeyState.REMOTE_CHANGED:
            report.kept_remote_changes.append(INDEX_POINTER_KEY)
            self._refuse_index(
                report,
                f"{INDEX_POINTER_KEY} changed in the bucket since the last sync (another machine published an "
                "index); pull it with `strategy-kb sync pull --with-index` instead of overwriting it",
            )
            return
        if corpus_diverges:
            self._refuse_index(
                report,
                "the corpus push left the bucket's corpus different from the local corpus the index was built "
                "from (conflicts, refused keys or kept remote changes); pull, re-index, then push again",
            )
            return
        etag = self._put(INDEX_POINTER_KEY, pointer.payload) or pointer.local_md5
        manifest[INDEX_POINTER_KEY] = {"etag": etag, "md5": pointer.local_md5}
        report.transferred.append(INDEX_POINTER_KEY)
        if pointer.state is KeyState.CONFLICT:
            report.resolved_conflicts[INDEX_POINTER_KEY] = "local"

    def _index_archive(self) -> bytes:
        """The local Chroma directory as a gzipped tar whose every member lives under chroma/."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            archive.add(self.store.chroma_path, arcname=CHROMA_DIRECTORY)
        return buffer.getvalue()

    def _pull_index(self, manifest: dict[str, dict[str, str]], report: SyncReport) -> None:
        """Replace the local Chroma directory with the archive index/LATEST names; record the pointer as synced."""
        remote_index_keys = self._remote_etags(INDEX_DIRECTORY)
        if INDEX_POINTER_KEY not in remote_index_keys:
            logger.info("no index published under %s%s; local index left as is", self.prefix, INDEX_DIRECTORY)
            return
        pointer = self.client.get_object(Bucket=self.bucket, Key=f"{self.prefix}{INDEX_POINTER_KEY}")
        pointer_bytes = pointer["Body"].read()
        corpus_version = pointer_bytes.decode(errors="replace").strip()
        if not CORPUS_VERSION_FORMAT.fullmatch(corpus_version):
            report.rejected_keys.append(INDEX_POINTER_KEY)
            logger.error("index/LATEST does not name a corpus version; index not pulled")
            return
        archive_key = f"{INDEX_DIRECTORY}/{corpus_version}/{INDEX_ARCHIVE_NAME}"
        if archive_key not in remote_index_keys:
            report.rejected_keys.append(archive_key)
            logger.error("index/LATEST names %s, which is not in the bucket; index not pulled", archive_key)
            return
        payload = self.client.get_object(Bucket=self.bucket, Key=f"{self.prefix}{archive_key}")["Body"].read()
        if not self._install_index(payload):
            report.rejected_keys.append(archive_key)
            return
        local_md5 = md5_bytes(pointer_bytes)
        manifest[INDEX_POINTER_KEY] = {"etag": str(pointer.get("ETag", "")).strip('"') or local_md5, "md5": local_md5}
        report.transferred.append(archive_key)

    def _install_index(self, payload: bytes) -> bool:
        """Extract an index archive into a scratch directory and swap it in as chroma/; False leaves all untouched.

        Only regular files and directories under chroma/ are accepted. An archive with any other member (an
        absolute path, a `..`, a link, or anything outside chroma/ such as corpus/, raw/ or sync_manifest.json)
        is refused whole, so an archive can never write outside the index directory.
        """
        self.store.root.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix=".index-pull-", dir=self.store.root))
        try:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                members = archive.getmembers()
                unsafe = [member.name for member in members if not is_index_member(member)]
                if unsafe:
                    logger.error("index archive refused: unsafe or non-chroma/ members %s", unsafe[:REPORTED_MEMBERS])
                    return False
                archive.extractall(scratch, members=members, filter="data")
            extracted = scratch / CHROMA_DIRECTORY
            if not extracted.is_dir():
                logger.error("index archive refused: it holds no chroma/ directory")
                return False
            swap_directory(extracted, self.store.chroma_path)
        except tarfile.TarError as error:
            logger.error("index archive refused: %s", error)
            return False
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return True
