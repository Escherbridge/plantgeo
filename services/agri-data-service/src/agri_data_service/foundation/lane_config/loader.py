"""Load `lanes/*.toml` and `lanes/_providers/*.toml` against one `Region`; a bad lane is quarantined (S8).

Lazy by construction: nothing here reads a file until `load_lane_configs` is called. See
`foundation/lane_config/AGENTS.md` "Loader" for the invariant list and the quarantine rules.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ValidationError

from agri_data_service.foundation.lane_config.models import LaneConfig, ProviderConfig, lane_requires_probe_edge

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from agri_data_service.foundation.region.manifest import Region

#: Overrides the lanes directory; unset, `default_lanes_directory()` finds it beside `src/`.
LANES_DIRECTORY_ENV_VAR: Final = "PLANTGEO_LANES_DIRECTORY"
PROVIDERS_SUBDIRECTORY: Final = "_providers"
LANE_FILE_GLOB: Final = "*.toml"

#: loader.py -> lane_config -> foundation -> agri_data_service -> src -> the service root, which
#: holds `lanes/` in the repo (`services/agri-data-service/`) and in both images (`/app`,
#: `/app/agri-service`), because `uv sync` installs the project editable from `src/`.
_SERVICE_ROOT_PARENT_INDEX: Final = 4


class LaneDirectoryError(RuntimeError):
    """The lanes directory itself is absent: a packaging fault for every lane, not one lane's error."""


@dataclass(frozen=True, slots=True)
class LaneQuarantine:
    """One lane file that did not load, and every reason why; its siblings are unaffected (S8)."""

    lane_id: str
    path: Path
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderLoadFailure:
    """One provider file that did not load; every lane on that provider is quarantined with it."""

    provider_id: str
    path: Path
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LaneConfigSet:
    """Everything one load produced: the healthy lanes and providers, and what was quarantined."""

    directory: Path
    region_slug: str
    providers: Mapping[str, ProviderConfig]
    provider_failures: Mapping[str, ProviderLoadFailure]
    lanes: Mapping[str, LaneConfig]
    quarantined: Mapping[str, LaneQuarantine]

    def provider_of(self, lane: LaneConfig) -> ProviderConfig | None:
        """The loaded provider an ingest lane reads, or None for a transform."""
        return None if lane.source is None else self.providers.get(lane.source.provider)

    def requires_probe_edge(self, lane: LaneConfig) -> bool:
        """Whether this lane's strategy must implement `probe_edge` (S6, S19)."""
        return lane_requires_probe_edge(lane, self.provider_of(lane))


def default_lanes_directory() -> Path:
    """`PLANTGEO_LANES_DIRECTORY` when set, else `lanes/` beside `src/` (the repo and both images alike)."""
    configured = os.environ.get(LANES_DIRECTORY_ENV_VAR, "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[_SERVICE_ROOT_PARENT_INDEX] / "lanes"


def _validation_reasons(error: ValidationError) -> tuple[str, ...]:
    reasons: list[str] = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"])
        reasons.append(f"{location}: {detail['msg']}" if location else str(detail["msg"]))
    return tuple(reasons)


def _read_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> tuple[ModelT | None, tuple[str, ...]]:
    """Parse one TOML file into `model`: (the model, or None with every reason it failed).

    The file stem is the identity: a declared `id` must equal it, so ids are unique by construction
    and a broken file is quarantined under its own name, never under a sibling's id.
    """
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return None, (f"{path.name} is not readable TOML: {error}",)
    try:
        parsed = model.model_validate(raw)
    except ValidationError as error:
        return None, _validation_reasons(error)
    declared_id = raw.get("id")
    if declared_id != path.stem:
        return None, (f"id {declared_id!r} must equal the file name stem {path.stem!r}",)
    return parsed, ()


def _load_providers(directory: Path) -> tuple[dict[str, ProviderConfig], dict[str, ProviderLoadFailure]]:
    providers: dict[str, ProviderConfig] = {}
    failures: dict[str, ProviderLoadFailure] = {}
    provider_directory = directory / PROVIDERS_SUBDIRECTORY
    if not provider_directory.is_dir():
        return providers, failures
    for path in sorted(provider_directory.glob(LANE_FILE_GLOB)):
        provider, reasons = _read_model(path, ProviderConfig)
        if provider is None:
            failures[path.stem] = ProviderLoadFailure(provider_id=path.stem, path=path, reasons=reasons)
        else:
            providers[path.stem] = provider
    return providers, failures


class _Quarantine:
    """Accumulates reasons per lane id while the cross-file rules run."""

    def __init__(self) -> None:
        self.reasons: dict[str, list[str]] = {}
        self.paths: dict[str, Path] = {}

    def add(self, lane_id: str, path: Path, reason: str) -> None:
        self.reasons.setdefault(lane_id, []).append(reason)
        self.paths[lane_id] = path

    def __contains__(self, lane_id: object) -> bool:
        return lane_id in self.reasons

    def freeze(self) -> Mapping[str, LaneQuarantine]:
        return MappingProxyType(
            {
                lane_id: LaneQuarantine(lane_id=lane_id, path=self.paths[lane_id], reasons=tuple(reasons))
                for lane_id, reasons in sorted(self.reasons.items())
            }
        )


def _region_and_provider_reasons(
    lane: LaneConfig,
    region: Region,
    providers: Mapping[str, ProviderConfig],
    provider_failures: Mapping[str, ProviderLoadFailure],
) -> list[str]:
    reasons: list[str] = []
    if lane.grid is not None and lane.grid not in region.analysis_lattices:
        reasons.append(f"grid {lane.grid!r} is not an analysis lattice of region {region.slug!r}")
    source = lane.source
    if source is None:
        return reasons
    provider = providers.get(source.provider)
    if provider is None:
        state = "failed to load" if source.provider in provider_failures else "has no provider file"
        reasons.append(f"provider {source.provider!r} {state}")
    else:
        for role, endpoint in (("endpoint", source.endpoint), ("history.endpoint", source.history.endpoint)):
            if endpoint is not None and endpoint not in provider.endpoints:
                reasons.append(f"{role} {endpoint!r} is not an endpoint of provider {provider.id!r}")
    uncovered = source.coverage_claim().uncovered_country_codes(region)
    if uncovered:
        reasons.append(f"source coverage does not contain region {region.slug!r}: missing {list(uncovered)}")
    return reasons


def _asymmetric_conflicts(lanes: Mapping[str, LaneConfig], lane_files: Iterable[str]) -> Iterable[tuple[str, str]]:
    """`conflicts_with` must be symmetric; only the declaring lane is quarantined (S8; see `AGENTS.md`)."""
    known = set(lane_files)
    for lane_id, lane in lanes.items():
        for other in lane.conflicts_with:
            partner = lanes.get(other)
            if other not in known:
                yield lane_id, f"conflicts_with {other!r}, which has no lane file"
            elif partner is not None and lane_id not in partner.conflicts_with:
                yield lane_id, f"conflicts_with {other!r}, which does not list {lane_id!r} back"


def _duplicate_streams(lanes: Mapping[str, LaneConfig]) -> Iterable[tuple[str, str]]:
    owners: dict[str, list[str]] = {}
    for lane_id, lane in lanes.items():
        for stream in lane.streams:
            owners.setdefault(stream.slug, []).append(lane_id)
    for slug, lane_ids in owners.items():
        if len(lane_ids) > 1:
            for lane_id in lane_ids:
                yield lane_id, f"stream {slug!r} is also declared by {sorted(set(lane_ids) - {lane_id})}"


def _unresolved_inputs(lanes: Mapping[str, LaneConfig], quarantine: _Quarantine) -> Iterable[tuple[str, str]]:
    """Kahn's algorithm over `inputs`: a lane resolves once every input has; the rest are cyclic or orphaned."""
    healthy = {lane_id for lane_id in lanes if lane_id not in quarantine}
    resolved: set[str] = set()
    pending = set(healthy)
    progressed = True
    while progressed:
        progressed = False
        for lane_id in sorted(pending):
            missing = [name for name in lanes[lane_id].inputs if name not in healthy]
            if missing:
                yield lane_id, f"inputs {missing} are missing or quarantined"
                healthy.discard(lane_id)
                pending.discard(lane_id)
                progressed = True
            elif all(name in resolved for name in lanes[lane_id].inputs):
                resolved.add(lane_id)
                pending.discard(lane_id)
                progressed = True
    for lane_id in sorted(pending):
        yield lane_id, f"inputs {list(lanes[lane_id].inputs)} never resolve: a cycle, or downstream of one"


def load_lane_configs(directory: Path, region: Region) -> LaneConfigSet:
    """Load every lane and provider TOML under `directory`, checked against `region` (spec §4.1).

    Never raises for a lane's content: a lane that fails a rule is returned in `quarantined` with
    every reason, and the rest load (S8). Raises `LaneDirectoryError` only when `directory` is absent.
    """
    if not directory.is_dir():
        raise LaneDirectoryError(f"lanes directory {directory} does not exist; set {LANES_DIRECTORY_ENV_VAR}")
    providers, provider_failures = _load_providers(directory)
    quarantine = _Quarantine()
    parsed: dict[str, LaneConfig] = {}
    paths: dict[str, Path] = {}
    for path in sorted(directory.glob(LANE_FILE_GLOB)):
        lane, reasons = _read_model(path, LaneConfig)
        lane_id = path.stem
        paths[lane_id] = path
        if lane is None:
            for reason in reasons:
                quarantine.add(lane_id, path, reason)
            continue
        parsed[lane_id] = lane
        for reason in _region_and_provider_reasons(lane, region, providers, provider_failures):
            quarantine.add(lane_id, path, reason)
    for lane_id, reason in (*_asymmetric_conflicts(parsed, paths), *_duplicate_streams(parsed)):
        quarantine.add(lane_id, paths[lane_id], reason)
    for lane_id, reason in tuple(_unresolved_inputs(parsed, quarantine)):
        quarantine.add(lane_id, paths[lane_id], reason)
    healthy = {lane_id: lane for lane_id, lane in sorted(parsed.items()) if lane_id not in quarantine}
    return LaneConfigSet(
        directory=directory,
        region_slug=region.slug,
        providers=MappingProxyType(dict(sorted(providers.items()))),
        provider_failures=MappingProxyType(dict(sorted(provider_failures.items()))),
        lanes=MappingProxyType(healthy),
        quarantined=quarantine.freeze(),
    )


__all__ = [
    "LANES_DIRECTORY_ENV_VAR",
    "PROVIDERS_SUBDIRECTORY",
    "LaneConfigSet",
    "LaneDirectoryError",
    "LaneQuarantine",
    "ProviderLoadFailure",
    "default_lanes_directory",
    "load_lane_configs",
]
