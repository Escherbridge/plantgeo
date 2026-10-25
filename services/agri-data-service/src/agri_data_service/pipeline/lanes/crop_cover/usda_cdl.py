"""`crop-cover-usda-maintain`: USDA CDL edition identity, dark (`enabled = false`) until activation decides more.

Strategy key `crop_cover.usda_cdl` (spec S14, §7 "crop-cover-usda-maintain (shadow)"). Proves exactly the
settle semantics spec §7 names for this lane -- **Edition identity** -- by re-asking the same lightweight,
unweighted catalogue query `source.py::capture()` already makes once per year (`/query?where=1=1&outFields=
OBJECTID,Name&returnGeometry=false&f=json`) and binding it to exactly one admitted release year via
`source.py::catalog_record`. It never fetches a tile: the multi-gigabyte regional capture stays
`capture()`'s own job, run today only by the legacy `pipeline/direct/crop_cover/forward.py` `maintain`
operator. Imports only `pipeline/runner/contract.py` and this package (`products.py`, `source.py`); why each
rule holds: `pipeline/lanes/crop_cover/AGENTS.md`, `pipeline/lanes/AGENTS.md`.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.lanes.crop_cover.products import RELEASE_DAYS
from agri_data_service.pipeline.lanes.crop_cover.source import canonical_bytes, catalog_record, sha256
from agri_data_service.pipeline.runner.contract import SourceRequest, SourceResponse, Unsettled, Written

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.runner.contract import DayContext, ProviderClient, Settlement

#: The provider endpoint this lane's one unit kind is sent on (`lanes/_providers/usda-cdl.toml`); the
#: catalogue query itself, not a tile, so this endpoint name is a loose fit for what it actually asks --
#: USDA ships no distinct "catalogue" endpoint, and `source.py` already queries the image-service root.
EDITION_IDENTITY_ENDPOINT: Final = "image-service"

#: The catalogue query `source.py::_json_get(client, "/query", ...)` sends once per captured year; sent
#: identically for every admitted release day asked this turn, since USDA's catalogue is not year-filtered
#: server-side -- `catalog_record` picks this unit's year out of the one shared answer, client-side.
CATALOGUE_QUERY_PARAMETERS: Final[tuple[tuple[str, str], ...]] = (
    ("where", "1=1"),
    ("outFields", "OBJECTID,Name"),
    ("returnGeometry", "false"),
    ("f", "json"),
)

#: `day -> admitted crop year`, the inverse of `RELEASE_DAYS` (release day -> crop year is one-to-one).
_YEAR_BY_RELEASE_DAY: Final[Mapping[Any, int]] = {release_day: year for year, release_day in RELEASE_DAYS.items()}

#: Env override for a future activation's local capture scratch root; unset resolves under the OS temp
#: directory. See `_runner_scratch_dir`.
SCRATCH_DIR_ENVIRONMENT_VARIABLE: Final = "AGRI_CROP_COVER_SCRATCH_DIR"

#: This lane's own subdirectory of the OS temp root, never a bare `tempfile.gettempdir()` (every other
#: service tenant of the shared temp root gets its own leaf, same shape as `burn_severity/daily.py`'s
#: `mkdtemp(prefix="plantgeo-mtbs-current-")` convention).
_SCRATCH_LEAF: Final = "agri-crop-cover"


def _year_for_release_day(day: date) -> int | None:
    """The admitted crop year `day` names, or `None` when `day` is not one of the four release days."""
    return _YEAR_BY_RELEASE_DAY.get(day)


def _matching_record(year: int, responses: Sequence[SourceResponse]) -> dict[str, Any]:
    """This year's one matching catalogue record out of the unit's answer.

    Raises exactly as `catalog_record` does (ambiguous or missing identity), which the runner turns
    into this day's `strategy_error`.
    """
    catalog = responses[0].payload
    if not isinstance(catalog, Mapping):
        raise TypeError("the CDL catalogue answer was not a JSON object")
    return catalog_record(dict(catalog), year)


def _runner_scratch_dir(environment: Mapping[str, str] | None = None) -> Path:
    """Where a future activation would stage one annual capture; env override, else the OS temp root.

    `source.py::capture()` already takes its output directory from its OWN caller (`CaptureConfig.output`,
    today only legacy's `--capture-root`); this lane is dark, so nothing calls this yet. It exists so the
    eventual config-runner wiring (decided at activation, like land-context's own reconcile-mapping
    carve-out) inherits ONE answer to "where", resolved the same portable way every other local-scratch
    caller in this service already resolves it (`tempfile.gettempdir()`/`mkdtemp`) -- never a hardcoded
    `/tmp` path, which is also simply wrong on this service's own Windows development hosts.

    PRIVATE and deliberately NOT a public answer: it names "where", never "how" -- it does not say,
    and this slice does not decide, how a config-lane `fetch()` would ever drive the existing
    14,400-second / 4-GiB regional tile capture (`source.py::MAX_CAPTURE_SECONDS`, `MAX_CAPTURE_BYTES`)
    inside this TOML's own `turn_timeout_seconds = 300`. That mismatch is part of the same deferred
    activation decision this package's own `AGENTS.md` ("usda_cdl.py and the dark TOML") already
    defers; this function is a stub for whichever future slice resolves it, not working surface today.
    """
    source = os.environ if environment is None else environment
    override = source.get(SCRATCH_DIR_ENVIRONMENT_VARIABLE, "").strip()
    return Path(override) if override else Path(tempfile.gettempdir()) / _SCRATCH_LEAF


@dataclass(frozen=True)
class UsdaCdlEditionIdentityStrategy:
    """One unit per admitted release day; proves which raster edition answers it, never its pixels."""

    def release_days(self, first: date, last: date) -> list[date]:
        """Only the admitted CDL release days inside `[first, last]`; every other day is never owed."""
        return sorted(day for day in RELEASE_DAYS.values() if first <= day <= last)

    def plan_requests(  # noqa: ARG002 - the Protocol's lane and region
        self, days: Sequence[date], lane: LaneConfig, region: Region
    ) -> list[SourceRequest]:
        """One catalogue-identity unit per asked release day; the runner only ever asks `release_days` values."""
        requests: list[SourceRequest] = []
        for day in sorted(set(days)):
            year = _year_for_release_day(day)
            if year is None:
                continue
            requests.append(
                SourceRequest(
                    unit=f"edition:{year}",
                    endpoint=EDITION_IDENTITY_ENDPOINT,
                    days=(day,),
                    parameters=CATALOGUE_QUERY_PARAMETERS,
                )
            )
        return requests

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """ONE catalogue send per unit; the runner's ladder owns every retry and status."""
        response = await client.get(request.endpoint, request.query())
        catalog = json.loads(response.body)
        return SourceResponse(request=request, body=response.body, retrieved_at=response.retrieved_at, payload=catalog)

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:
        """Written once the catalogue names exactly one raster for this release year; never a partial day."""
        del context
        year = _year_for_release_day(day)
        if year is None or not responses:
            return Unsettled("unsettled", f"{day.isoformat()} names no answered CDL edition yet")
        record = _matching_record(year, responses)
        digest = sha256(canonical_bytes(record))
        identity = frozenset({f"edition:{year}"})
        return Written(
            expected_units=1,
            present_units=1,
            source_digest=digest,
            expected_unit_ids=identity,
            present_unit_ids=identity,
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One edition-identity row; called only after `settle` already proved this day's one record."""
        year = _year_for_release_day(day)
        record = _matching_record(year, responses)  # type: ignore[arg-type] - settle already proved `year` is not None
        return pa.table(
            {
                "observed_year": pa.array([year], pa.int32()),
                "release_day": pa.array([day], pa.date32()),
                "object_id": pa.array([record.get("OBJECTID")], pa.int64()),
                "ingested_at": pa.array([responses[0].retrieved_at], pa.timestamp("us", tz="UTC")),
            }
        )


STRATEGY: Final = UsdaCdlEditionIdentityStrategy()

__all__ = [
    "CATALOGUE_QUERY_PARAMETERS",
    "EDITION_IDENTITY_ENDPOINT",
    "SCRATCH_DIR_ENVIRONMENT_VARIABLE",
    "STRATEGY",
    "UsdaCdlEditionIdentityStrategy",
]
