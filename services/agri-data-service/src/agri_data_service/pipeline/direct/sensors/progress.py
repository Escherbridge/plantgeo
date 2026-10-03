"""Resumable day sweeps: which roster stations a due day was already asked about, and what they answered.

Scratch, never serving data: one checksummed JSON object per due day under an operational namespace no
coverage listing walks. See this package's `AGENTS.md`, "A sweep resumes across turns".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.canonical import canonical_json, sha256_digest

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from agri_data_service.ingest.sensors import SensorStation
    from agri_data_service.pipeline.parquet.objectstore import ObjectStoreBackend

#: Relative root of every sensors sweep scratch object; outside any `layer=` prefix, like
#: `source-response-checkpoints/v1/`, so no partition or availability listing can ever see one.
SWEEP_PROGRESS_ROOT: Final = "source-sweep-progress/v1/sensors/"
#: v2: `unavailable` moved from a set to a station -> attempt-count map (`SENSORS_STATION_ATTEMPT_CAP`).
#: A v1 payload fails its shape check and is discarded as `DISCARD_UNREADABLE`, costing one re-sweep.
SWEEP_PROGRESS_VERSION: Final = "sensors-sweep-progress-v2"
#: About 600 stations x about 1 KB per parsed winner; 8 MiB refuses a runaway object, not a real one.
SWEEP_PROGRESS_MAX_BYTES: Final = 8 * 1024 * 1024
_CONTENT_TYPE: Final = "application/json"
_DAY_PREFIX: Final = "day="
_SUFFIX: Final = ".json"

#: Why a held scratch object was dropped instead of resumed.
DISCARD_ROSTER_CHANGED: Final = "roster_changed"
DISCARD_UNREADABLE: Final = "unreadable"

#: Total attempts a day's sweep spends on one station before sweeping around it for good. Without a
#: cap, `remaining()` would re-ask a permanently-down station forever; with one, the day still
#: "fully sweeps" and publishes, naming the station's reports absent rather than owing it forever.
SENSORS_STATION_ATTEMPT_CAP: Final = 3


def roster_sha256(stations: Iterable[SensorStation]) -> str:
    """Identity of one roster: the digest of its sorted station identifiers."""
    return sha256_digest(canonical_json(sorted({station.station_identifier for station in stations})))


@dataclass(slots=True)
class DaySweepProgress:
    """One due day's sweep so far, bound to the roster it was swept against."""

    day: date
    roster_sha256: str
    #: Station -> the reports it answered with; an empty list is an asked station with no report that day.
    answered: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    #: Stations whose request has raised at least once this day, mapped to how many attempts have been
    #: spent on them so far. Re-asked on later turns until answered or `SENSORS_STATION_ATTEMPT_CAP` is
    #: reached, at which point the day sweeps around the station rather than owing it forever.
    unavailable: dict[str, int] = field(default_factory=dict)

    def remaining(self, stations: Sequence[SensorStation]) -> list[SensorStation]:
        """The roster stations this day has not answered, and has not exhausted its attempts on, in roster order."""
        return [
            station
            for station in stations
            if station.station_identifier not in self.answered
            and self.unavailable.get(station.station_identifier, 0) < SENSORS_STATION_ATTEMPT_CAP
        ]

    def record_unavailable(self, station_identifier: str) -> None:
        """One more failed attempt for a station whose request raised this turn."""
        self.unavailable[station_identifier] = self.unavailable.get(station_identifier, 0) + 1

    @property
    def stations_asked(self) -> int:
        return len(self.answered) + len(self.unavailable)

    def records(self) -> list[dict[str, object]]:
        """Every report the sweep holds, in station order so a resumed day publishes deterministically."""
        return [record for station in sorted(self.answered) for record in self.answered[station]]

    def to_payload(self) -> bytes:
        value = {
            "schema_version": SWEEP_PROGRESS_VERSION,
            "day": self.day.isoformat(),
            "roster_sha256": self.roster_sha256,
            "answered": self.answered,
            "unavailable": dict(sorted(self.unavailable.items())),
        }
        return canonical_json({"progress": value, "sha256": sha256_digest(canonical_json(value))}).encode()


def _decode(payload: bytes, *, day: date) -> DaySweepProgress:
    """Verify the envelope checksum, version and day before any field is trusted."""
    decoded = json.loads(payload)
    if not isinstance(decoded, dict) or set(decoded) != {"progress", "sha256"}:
        raise ValueError("invalid sweep progress envelope")
    value = decoded["progress"]
    if not isinstance(value, dict) or sha256_digest(canonical_json(value)) != decoded["sha256"]:
        raise ValueError("sweep progress checksum mismatch")
    if value.get("schema_version") != SWEEP_PROGRESS_VERSION or value.get("day") != day.isoformat():
        raise ValueError("sweep progress version or day mismatch")
    answered, unavailable, roster = value.get("answered"), value.get("unavailable"), value.get("roster_sha256")
    if not isinstance(answered, dict) or not isinstance(unavailable, dict) or not isinstance(roster, str):
        raise ValueError("sweep progress fields have the wrong shape")
    if not all(isinstance(reports, list) for reports in answered.values()):
        raise ValueError("sweep progress answers have the wrong shape")
    if not all(isinstance(attempts, int) for attempts in unavailable.values()):
        raise ValueError("sweep progress attempt counts have the wrong shape")
    return DaySweepProgress(
        day=day,
        roster_sha256=roster,
        answered=answered,
        unavailable={str(station): int(attempts) for station, attempts in unavailable.items()},
    )


@dataclass(frozen=True, slots=True)
class HeldProgress:
    """What `SweepProgressStore.resume` found for one day: the progress to continue, or why there is none."""

    progress: DaySweepProgress
    resumed: bool
    discarded: str | None = None
    #: False when this turn must not write the day's scratch at all (save OR clear): a roster mismatch
    #: seen while THIS turn's own roster is degraded (`allow_roster_discard=False`) is not trusted
    #: enough to judge the day's real saved progress stale, so that progress is left untouched for a
    #: later, healthy turn to resume.
    persist: bool = True


@dataclass(frozen=True, slots=True)
class SweepProgressStore:
    """Read, write and retire sweep scratch through the lane's own bucket backend. Never raises."""

    backend: ObjectStoreBackend
    #: Absolute key of `SWEEP_PROGRESS_ROOT` under the store's prefix (`ObjectStore.key_for`).
    root_key: str

    def key(self, day: date) -> str:
        return f"{self.root_key}{_DAY_PREFIX}{day.isoformat()}{_SUFFIX}"

    def resume(self, day: date, *, roster: str, allow_roster_discard: bool = True) -> HeldProgress:
        """The day's held progress if it was swept against this roster; otherwise a fresh, empty sweep.

        `allow_roster_discard=False` (this turn's own roster is degraded: `ingest/sensors.py::fetch_station_roster`
        could not reach every configured state) turns a roster mismatch into an UNPERSISTED ephemeral
        sweep rather than a discard: the day is swept fresh for this turn's report, but the real saved
        scratch -- built against the last roster every state answered for -- is neither cleared nor
        overwritten, so a transient state outage costs this turn's progress once, not the day's whole
        history twice (once losing it here, again when the state recovers and the roster no longer
        matches what this turn would have saved).
        """
        fresh = DaySweepProgress(day=day, roster_sha256=roster)
        try:
            payload = self.backend.get(self.key(day))
            if payload is None:
                return HeldProgress(progress=fresh, resumed=False)
            if len(payload) > SWEEP_PROGRESS_MAX_BYTES:
                raise ValueError("sweep progress exceeds its byte ceiling")
            held = _decode(payload, day=day)
        except Exception:  # an unreadable scratch costs a re-sweep, never the turn
            self.clear(day)
            return HeldProgress(progress=fresh, resumed=False, discarded=DISCARD_UNREADABLE)
        if held.roster_sha256 != roster:
            if not allow_roster_discard:
                return HeldProgress(progress=fresh, resumed=False, persist=False)
            # A day is published from ONE roster: mixing two would serve stations neither roster holds.
            self.clear(day)
            return HeldProgress(progress=fresh, resumed=False, discarded=DISCARD_ROSTER_CHANGED)
        return HeldProgress(progress=held, resumed=True)

    def save(self, progress: DaySweepProgress) -> bool:
        """Persist one day's progress; False (progress lost, never the turn) when the write fails."""
        try:
            payload = progress.to_payload()
            if len(payload) > SWEEP_PROGRESS_MAX_BYTES:
                return False
            self.backend.put(self.key(progress.day), payload, content_type=_CONTENT_TYPE)
        except Exception:
            return False
        return True

    def clear(self, day: date) -> None:
        """Retire one day's scratch: it published, or it must be swept afresh."""
        try:
            self.backend.delete(self.key(day))
        except Exception:  # a stale scratch is pruned once its day stops being due
            return

    def prune(self, keep: Iterable[date]) -> int:
        """Delete scratch for every day no longer due (published, blocked or aged out of NWS retention)."""
        kept = frozenset(keep)
        removed = 0
        try:
            listed = [entry.key for entry in self.backend.list_objects(self.root_key)]
        except Exception:
            return 0
        for key in listed:
            rendered = key[len(self.root_key) :].removeprefix(_DAY_PREFIX).removesuffix(_SUFFIX)
            try:
                day = date.fromisoformat(rendered)
            except ValueError:
                continue
            if day not in kept:
                self.clear(day)
                removed += 1
        return removed


__all__ = [
    "DISCARD_ROSTER_CHANGED",
    "DISCARD_UNREADABLE",
    "SENSORS_STATION_ATTEMPT_CAP",
    "SWEEP_PROGRESS_MAX_BYTES",
    "SWEEP_PROGRESS_ROOT",
    "SWEEP_PROGRESS_VERSION",
    "DaySweepProgress",
    "HeldProgress",
    "SweepProgressStore",
    "roster_sha256",
]
