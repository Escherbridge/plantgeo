"""Offline SSURGO commands: census, capture, prepare, verify-replay, validate, stage, release.

Ported from `archive/freshness-integrated-candidate-20260914`'s `pipeline/direct/soil_survey/
__main__.py`: slice S1 wired `areas` and `capture`; slice S2 adds the candidate verbs
(`AGENTS.md`, "Operational entry point"). Every command is dry-run unless `--apply` is given. Only
`stage --apply` and `release --apply` write to a bucket, and only behind `require_stage_authorised`;
nothing here advances admission or reports `serving_published=true`, this package is not a
registered lane (`pipeline/parquet/lane_registry.py` still refuses it) and no cron calls it.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

import httpx
from pydantic import ValidationError

from agri_data_service.config import settings
from agri_data_service.foundation.geography.bounding_box import BoundingBox, inline_bbox_value, parse_bounding_box
from agri_data_service.foundation.region import load_region
from agri_data_service.foundation.soil_survey.receipts import (
    AreaCensusEntry,
    AreaInventory,
    SoilSurveyError,
    digest,
)
from agri_data_service.pipeline.direct.soil_survey.capture import (
    DEFAULT_TIME_BUDGET_SECONDS,
    capture_area,
    save_blob,
    save_checkpoint,
)
from agri_data_service.pipeline.direct.soil_survey.local_storage import LocalCandidateStorage
from agri_data_service.pipeline.direct.soil_survey.prepare import (
    DEFAULT_PREPARATION_SECONDS,
    load_candidate_manifest,
    prepare_candidate,
    verify_candidate_replay,
)
from agri_data_service.pipeline.direct.soil_survey.source import (
    AREA_INVENTORY_COLUMNS,
    DEFAULT_FETCH_TIMEOUT_SECONDS,
    area_inventory_query,
    fetch,
    table_rows,
)
from agri_data_service.pipeline.direct.soil_survey.stage import (
    DEFAULT_UPLOAD_BUDGET_SECONDS,
    build_release,
    publish_candidate,
    publish_release,
    read_journal,
    require_stage_authorised,
    stage_plan,
)
from agri_data_service.pipeline.parquet.availability_storage import BotoAvailabilityStorage
from agri_data_service.pipeline.validation.soil_survey import (
    HttpxSoilSurveySdaClient,
    candidate_validation_groups,
    validate_soil_survey_candidate,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from agri_data_service.pipeline.parquet.availability_storage import AvailabilityStorage

COMMANDS: Final = ("areas", "capture", "prepare", "verify-replay", "validate", "stage", "release")

#: `--census-tile-degrees`' own default: the Go-2 live re-run over the full pnw envelope (16x9 deg)
#: at this edge measured 15.4 s median / 23.7 s max / 11.3 s min across 33 tiles reached, zero
#: timeouts (`AGENTS.md`, "Census tiling"; review finding 5 -- the prior comment's "well under 3.7 s"
#: claim was unmeasured extrapolation, not evidence).
DEFAULT_CENSUS_TILE_DEGREES = 2.0
#: `--request-timeout-seconds`' own upper bound: generous relative to every observed per-tile
#: latency (`AGENTS.md`, "Census tiling"), but still a bound, not an unlimited caller override.
MAX_REQUEST_TIMEOUT_SECONDS = 120.0
#: `_axis_edges`' own float-noise guard: a `high - low` that divides `step` exactly can still land a
#: hair below the true integer count in floating point (review finding 1), so this is subtracted
#: before `math.ceil` rather than compared against directly.
_AXIS_EDGE_EPSILON = 1e-9
#: The literal `SoilSurveyError` message `source.py::table_rows` raises for SDA's own bare `{}`
#: answer -- distinct from every other malformed-payload message that module raises, and the only
#: one `_areas` treats as "this tile has zero rows" rather than a fatal error (live-run defect).
_SDA_NO_ROWS_MESSAGE = "SDA returned no rows"


def _resolve_scope(arguments: argparse.Namespace) -> tuple[BoundingBox, str, bool]:
    """The census envelope this invocation will use, the region slug, and whether it was overridden.

    Revision 2 (F1, F9, Q2; P1-01/SEC-1): the Region envelope (`foundation/region/load_region().
    envelope`) is the ONLY default -- never an inherited `INGEST_BBOX` (set on
    `plantgeo-job-executor`, `docs/env-vars.md:64`), which is the client's narrower
    `default_camera_envelope`, not the Region envelope the owner settled on for this census (Q2).
    `--bbox` is accepted only as an explicit operator override, and an override that differs from
    the region's own envelope is refused unless `--allow-non-region-scope` is also given, so an
    inherited environment variable can never silently narrow a census with no trace.
    """
    region = load_region()
    region_envelope: BoundingBox = (
        region.envelope.west,
        region.envelope.south,
        region.envelope.east,
        region.envelope.north,
    )
    if arguments.bbox is None:
        return region_envelope, region.slug, False
    bbox = parse_bounding_box(arguments.bbox)
    overridden = bbox != region_envelope
    if overridden and not arguments.allow_non_region_scope:
        raise SoilSurveyError(
            f"--bbox {bbox!r} differs from the {region.slug!r} region's own envelope "
            f"{region_envelope!r}; pass --allow-non-region-scope to census a different extent "
            "on purpose"
        )
    return bbox, region.slug, overridden


def _axis_edges(low: float, high: float, step: float) -> tuple[tuple[float, float], ...]:
    """One axis's own tile boundaries: `step`-wide bands, the last one clipped to `high`.

    Band count comes from `math.ceil((high - low) / step)` (index-computed), never from
    accumulating `cursor += step` in a loop (review finding 1): repeated float addition can leave
    `cursor` a hair below `high` even when the division is exact, adding one spurious hairline-wide
    extra band at the high edge. See `AGENTS.md`, "Census tiling".
    """
    count = max(1, math.ceil((high - low) / step - _AXIS_EDGE_EPSILON))
    edges = [low + index * step for index in range(count)]
    edges.append(high)
    return tuple(itertools.pairwise(edges))


def _tile_grid(bbox: BoundingBox, tile_degrees: float) -> tuple[BoundingBox, ...]:
    """Partition `bbox` into a row-major grid of <= `tile_degrees`-edge tiles, covering it exactly.

    The final column and the final row are each clipped to the envelope's own east/north edge
    rather than padded past it (`_axis_edges`), so the tiles here union back to precisely `bbox` --
    never a sliver more, never a sliver less -- whether or not `tile_degrees` evenly divides the
    envelope's width or height. This is the Go-2 fix for Anomaly 1 (`AGENTS.md`, "Census tiling"):
    `__main__.py::_areas` queries each tile sequentially and unions the rows by area symbol, so no
    single `area_inventory_query` call ever spans more than one tile's extent.
    """
    west, south, east, north = bbox
    lon_edges = _axis_edges(west, east, tile_degrees)
    lat_edges = _axis_edges(south, north, tile_degrees)
    return tuple((lon0, lat0, lon1, lat1) for lat0, lat1 in lat_edges for lon0, lon1 in lon_edges)


def _validate_census_tile_degrees(tile_degrees: float) -> None:
    """`--census-tile-degrees` must be a positive, finite number of degrees.

    No upper bound (review finding 2, reversing the prior `<= envelope span` rule): `_axis_edges`
    already collapses a tile edge at or past the envelope's own longer dimension into a single
    untiled band on its own, so the bound added nothing there but refused every small `--bbox` --
    including the Go-1 pilot's own ~0.4 deg diagnostic bbox -- under the default 2.0 deg edge, since
    the default then exceeded the bbox's own span.
    """
    if not (math.isfinite(tile_degrees) and tile_degrees > 0):
        raise SoilSurveyError(
            f"--census-tile-degrees must be a positive, finite number of degrees; got {tile_degrees!r}"
        )


def _validate_request_timeout_seconds(request_timeout_seconds: float) -> None:
    """`--request-timeout-seconds` must be positive and within the generous local sanity bound."""
    if not (0 < request_timeout_seconds <= MAX_REQUEST_TIMEOUT_SECONDS):
        raise SoilSurveyError(
            "--request-timeout-seconds must be > 0 and <= "
            f"{MAX_REQUEST_TIMEOUT_SECONDS!r}; got {request_timeout_seconds!r}"
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=COMMANDS)
    result.add_argument("--root", type=Path, required=True)
    result.add_argument(
        "--area",
        action="append",
        default=[],
        help="capture: exactly one area; prepare: the shard's complete area list; validate: an optional subset",
    )
    result.add_argument(
        "--bbox",
        default=None,
        help="areas only: an explicit override of the Region envelope; requires "
        "--allow-non-region-scope unless it equals the region's own envelope exactly",
    )
    result.add_argument(
        "--allow-non-region-scope",
        action="store_true",
        help="areas only: permit --bbox to census an extent other than the Region envelope",
    )
    result.add_argument(
        "--census-tile-degrees",
        type=float,
        default=DEFAULT_CENSUS_TILE_DEGREES,
        help="areas only: edge length (degrees) of the sequential tile grid the census envelope is "
        "split into; must be a positive, finite number of degrees",
    )
    result.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=DEFAULT_FETCH_TIMEOUT_SECONDS,
        help=f"areas only: per-tile SDA request timeout in seconds; must be > 0 and <= {MAX_REQUEST_TIMEOUT_SECONDS!r}",
    )
    result.add_argument("--max-pages", type=int, default=8)
    result.add_argument("--page-size", type=int, default=500)
    result.add_argument(
        "--time-budget-seconds",
        type=float,
        default=None,
        help=f"capture (default {DEFAULT_TIME_BUDGET_SECONDS} s) or prepare/verify-replay "
        f"(default {DEFAULT_PREPARATION_SECONDS} s)",
    )
    result.add_argument("--shard", default=None, help="prepare only: the operator-declared shard id, e.g. WA-1")
    result.add_argument(
        "--manifest", default=None, help="verify-replay/validate/stage: the prepared shard manifest's SHA-256"
    )
    result.add_argument(
        "--shard-manifest",
        action="append",
        default=[],
        help="release only: one staged shard manifest SHA-256; repeat once per shard",
    )
    result.add_argument(
        "--bucket",
        default=None,
        help="stage/release: must equal OBJECT_STORE_BUCKET; --apply also needs SSURGO_STAGE_ALLOWED=1",
    )
    result.add_argument(
        "--include-source-evidence",
        action="store_true",
        help="stage only: also upload raw SDA responses (default: they stay on the capture volume)",
    )
    result.add_argument(
        "--upload-budget-seconds",
        type=float,
        default=DEFAULT_UPLOAD_BUDGET_SECONDS,
        help="stage only: wall-clock budget for this invocation; re-run to resume from the journal",
    )
    result.add_argument(
        "--from-bucket",
        action="store_true",
        help="validate only: read parts from the configured bucket instead of --root (reads only)",
    )
    result.add_argument(
        "--apply",
        action="store_true",
        help="Perform the named action (validate is a NETWORK step: one USDA SDA call per area); "
        "no command ever admits a release",
    )
    return result


async def _census_one_tile(
    client: httpx.AsyncClient, root: Path, tile: BoundingBox, request_timeout_seconds: float
) -> tuple[dict[str, object], str, tuple[dict[str, str | None], ...], float]:
    """Fetch, save and decode one tile's own `area_inventory_query` answer.

    Returns this tile's manifest entry (bbox, blob sha/byte-count, own query digest), that same
    query digest on its own, its decoded rows (`()` for SDA's own bare `{}` -- see `_areas`'s
    live-run-defect note), and its wall time.
    """
    query = area_inventory_query(tile)
    query_digest = digest(query.encode())
    started = time.perf_counter()
    payload = await fetch(client, query, timeout=request_timeout_seconds)
    elapsed = time.perf_counter() - started
    tile_blob = save_blob(root, payload)
    manifest_entry: dict[str, object] = {
        "bbox": list(tile),
        "response": tile_blob.sha256,
        "byte_count": tile_blob.byte_count,
        "query_sha256": query_digest,
    }
    try:
        rows = table_rows(payload, AREA_INVENTORY_COLUMNS)
    except SoilSurveyError as error:
        if str(error) != _SDA_NO_ROWS_MESSAGE:
            raise
        # Live-run defect: a rectangular tile grid over an irregular envelope legitimately produces
        # tiles with zero intersecting survey areas -- SDA answers those with its own bare `{}`,
        # which `table_rows` otherwise refuses as a fatal `SoilSurveyError` (F7). One empty tile
        # must contribute zero rows and let the census continue, not abort the other N-1 tiles the
        # way tile 33/40 did in the Go-2 live re-run.
        rows = ()
    return manifest_entry, query_digest, rows, elapsed


def _merge_tile_rows(vintages: dict[str, str], rows: tuple[dict[str, str | None], ...]) -> None:
    """Union one tile's own rows into the running `area -> saverest` map, in place."""
    for census_row in rows:
        area_symbol, saverest = census_row["areasymbol"], census_row["saverest"]
        if area_symbol is None or saverest is None:
            # P1-02: a NULL symbol or vintage is refused, not silently dropped -- the saved
            # AreaInventory must always match the response blobs it cites (AGENTS.md, "Bounds,
            # resume and missingness"). Non-SSURGO STATSGO2 rows are excluded upstream in
            # ssurgo_area_inventory.sql, not filtered here.
            raise SoilSurveyError(f"area-inventory row is missing its symbol or vintage: {census_row!r}")
        existing = vintages.get(area_symbol)
        if existing is not None and existing != saverest:
            # Census tiling's own new refusal: an area whose polygon spans a tile boundary can
            # legitimately answer from more than one tile, but never with two different vintages --
            # that would mean picking one silently, which this union never does.
            raise SoilSurveyError(
                f"{area_symbol}: area inventory contains a duplicate survey-area symbol with "
                f"conflicting vintages across tiles ({existing!r} vs {saverest!r})"
            )
        vintages[area_symbol] = saverest


async def _areas(arguments: argparse.Namespace) -> dict[str, object]:
    """Census the resolved envelope one sequential tile at a time and union the rows by area symbol.

    Go-2 fix for the Go-1 pilot's Anomaly 1 (`AGENTS.md`, "Census tiling"): a single
    `area_inventory_query` over the whole Region envelope hit `httpx.ReadTimeout` 3/3 in the pilot,
    while the same query at a small bbox answered in 3.7 s. Splitting the envelope into a grid
    (`_tile_grid`) and querying each tile SEQUENTIALLY -- never in parallel, matching every other
    politeness rule this CLI already keeps toward SDA -- keeps each individual call small while the
    `AreaInventory` receipt still records the *whole* resolved envelope, never a tile, as its scope.
    """
    bbox, region_slug, overridden = _resolve_scope(arguments)
    tile_degrees = arguments.census_tile_degrees
    _validate_census_tile_degrees(tile_degrees)
    request_timeout_seconds = arguments.request_timeout_seconds
    _validate_request_timeout_seconds(request_timeout_seconds)
    checkpoint_path = arguments.root / "areas.json"
    if checkpoint_path.exists():
        previous = AreaInventory.model_validate_json(checkpoint_path.read_bytes())
        if previous.envelope != bbox or previous.region != region_slug:
            # P1-06: refuse to silently replace a differing scope's census -- overwriting it would
            # orphan the previous response blob with nothing left pointing at it, and no auditor
            # could tell from areas.json alone that the scope had changed underneath it.
            raise SoilSurveyError(
                f"{checkpoint_path} already holds a census for region {previous.region!r} / "
                f"envelope {previous.envelope!r}; move it aside before recensusing a different scope"
            )
    tiles = _tile_grid(bbox, tile_degrees)
    vintages: dict[str, str] = {}
    tile_manifest: list[dict[str, object]] = []
    tile_query_digests: list[str] = []
    tile_seconds: list[float] = []
    async with httpx.AsyncClient(follow_redirects=False) as client:
        for tile in tiles:
            manifest_entry, query_digest, rows, elapsed = await _census_one_tile(
                client, arguments.root, tile, request_timeout_seconds
            )
            tile_manifest.append(manifest_entry)
            tile_query_digests.append(query_digest)
            tile_seconds.append(elapsed)
            _merge_tile_rows(vintages, rows)
    if not vintages:
        # P1-02/P1-06: an empty area-inventory response is refused outright; it is not a governed
        # absence and must never be recorded as `outcome: censused`.
        raise SoilSurveyError("area-inventory census returned zero areas; this is not a governed absence")
    entries: list[AreaCensusEntry] = []
    for area_symbol in sorted(vintages):
        try:
            entries.append(AreaCensusEntry(area=area_symbol, saverest=vintages[area_symbol]))
        except ValidationError as error:
            # Belt-and-suspenders alongside the SQL-side LEN(sac.areasymbol) = 5 filter: a symbol or
            # vintage that still does not fit the receipt's own shape is a refusal, not an uncaught
            # pydantic ValidationError.
            raise SoilSurveyError(f"area-inventory row failed validation: {area_symbol!r}: {error}") from error
    # `AreaInventory.response` is one `Blob` (Freeze 1, `receipts.py` untouched): with N tile
    # queries there is no single raw payload to point it at, so it points at a small manifest --
    # each tile's own bbox, its own separately-saved/independently-verifiable blob, AND its own
    # `query_sha256` -- rather than duplicating the tiles' bytes a second time. `query_commitment`
    # (review finding 3) digests `tile_degrees` plus every tile's OWN query digest, in tile order,
    # not just the tile bboxes: before this fix a changed `ssurgo_area_inventory.sql` body produced
    # the identical top-level `query_sha256` as the unchanged query, silently defeating F7's "query
    # bodies are frozen once a capture exists" for this receipt. Per-tile digests plus `tile_degrees`
    # fully reconstruct every query this census actually ran.
    manifest_payload = json.dumps({"tile_degrees": tile_degrees, "tiles": tile_manifest}, sort_keys=True).encode()
    response_blob = save_blob(arguments.root, manifest_payload)
    query_commitment = digest(
        json.dumps({"tile_degrees": tile_degrees, "tile_query_sha256": tile_query_digests}, sort_keys=True).encode()
    )
    try:
        inventory = AreaInventory(
            response=response_blob,
            query_sha256=query_commitment,
            checked_at=datetime.now(UTC),
            areas=tuple(entries),
            envelope=bbox,
            region=region_slug,
        )
    except ValidationError as error:
        # e.g. a duplicate area symbol across two rows (`unique_areas`): refused as a SoilSurveyError
        # like every other scope-integrity failure in this function, not a raw ValidationError.
        raise SoilSurveyError(f"area-inventory census failed validation: {error}") from error
    save_checkpoint(checkpoint_path, inventory)
    return {
        "outcome": "censused",
        "bbox": list(bbox),
        "region": region_slug,
        "region_envelope_override": overridden,
        "area_count": len(inventory.areas),
        "areas": [entry.area for entry in inventory.areas],
        "serving_published": False,
        "tiles_queried": len(tiles),
        "max_tile_seconds": max(tile_seconds) if tile_seconds else 0.0,
    }


async def _capture(arguments: argparse.Namespace) -> dict[str, object]:
    area = arguments.area[0]
    inventory_path = arguments.root / "areas.json"
    if inventory_path.exists():
        # P1-06 (optional guard from the plan, §"Freeze-1 completeness"): once a scope census
        # exists, every capture must draw its area from it (NFR-5), never from an operator's typo
        # or a literal outside the censused scope.
        inventory = AreaInventory.model_validate_json(inventory_path.read_bytes())
        known = {entry.area for entry in inventory.areas}
        if area not in known:
            raise SoilSurveyError(
                f"{area} is not in this root's area-inventory census ({inventory_path}); "
                "run `areas --apply` first, or capture an area the census actually named"
            )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        capture = await capture_area(
            client,
            arguments.root,
            area,
            max_pages=arguments.max_pages,
            page_size=arguments.page_size,
            time_budget_seconds=(
                DEFAULT_TIME_BUDGET_SECONDS if arguments.time_budget_seconds is None else arguments.time_budget_seconds
            ),
        )
    return {
        "outcome": "captured" if capture.closing is not None else "incomplete",
        "area": capture.opening.area,
        "rows": sum(page.row_count for page in capture.pages),
        "expected_rows": capture.opening.count,
        "serving_published": False,
    }


def _preparation_budget(arguments: argparse.Namespace) -> float:
    budget: float | None = arguments.time_budget_seconds
    return DEFAULT_PREPARATION_SECONDS if budget is None else budget


def _configured_bucket() -> str | None:
    """The bucket `stage`/`release` must be pointed at explicitly; a seam for tests."""
    return settings.object_store_bucket


def _authorise_bucket_write(arguments: argparse.Namespace) -> None:
    require_stage_authorised(arguments.bucket, configured_bucket=_configured_bucket(), environ=os.environ)


def _prepare(arguments: argparse.Namespace) -> dict[str, object]:
    candidate, sha = prepare_candidate(
        arguments.root, arguments.shard, arguments.area, time_budget_seconds=_preparation_budget(arguments)
    )
    return {
        "outcome": "prepared",
        "shard": candidate.shard,
        "manifest_sha256": sha,
        "areas": [capture.opening.area for capture in candidate.areas],
        "parts": len(candidate.parts),
        "native_rows": sum(capture.opening.count for capture in candidate.areas),
        "repaired_rows": sum(quality.repaired_rows for quality in candidate.quality),
        "labelled_rows": sum(quality.labelled_rows for quality in candidate.quality),
        "serving_published": False,
    }


def _verify_replay(arguments: argparse.Namespace) -> dict[str, object]:
    candidate = verify_candidate_replay(
        arguments.root, arguments.manifest, time_budget_seconds=_preparation_budget(arguments)
    )
    return {
        "outcome": "replayed",
        "shard": candidate.shard,
        "manifest_sha256": arguments.manifest,
        "serving_published": False,
    }


async def _validate(arguments: argparse.Namespace) -> dict[str, object]:
    """NETWORK step: one USDA SDA census call per area, reading parts locally unless --from-bucket."""
    candidate, _ = load_candidate_manifest(arguments.root, arguments.manifest)
    groups = candidate_validation_groups(candidate, arguments.area)
    storage: AvailabilityStorage = (
        BotoAvailabilityStorage.from_settings() if arguments.from_bucket else LocalCandidateStorage(arguments.root)
    )
    findings: list[dict[str, object]] = []
    async with httpx.AsyncClient(follow_redirects=False) as client:
        source = HttpxSoilSurveySdaClient(client=client)
        for group in groups:
            report = await validate_soil_survey_candidate(candidate, storage, source, survey_area_symbols=group)
            findings.extend(asdict(finding) for finding in report.findings)
    return {
        "outcome": "findings" if findings else "validated",
        "shard": candidate.shard,
        "manifest_sha256": arguments.manifest,
        "storage": "bucket" if arguments.from_bucket else "local",
        "groups": [list(group) for group in groups],
        "findings": findings,
        "serving_published": False,
    }


def _stage(arguments: argparse.Namespace) -> dict[str, object]:
    _authorise_bucket_write(arguments)
    report = publish_candidate(
        arguments.root,
        arguments.manifest,
        BotoAvailabilityStorage.from_settings(),
        bucket=arguments.bucket,
        prefix=settings.object_store_prefix,
        include_source_evidence=arguments.include_source_evidence,
        upload_budget_seconds=arguments.upload_budget_seconds,
    )
    return {"outcome": "candidate_staged", **asdict(report), "admitted": False, "serving_published": False}


def _release(arguments: argparse.Namespace) -> dict[str, object]:
    _authorise_bucket_write(arguments)
    release, payload = build_release(
        arguments.root, arguments.shard_manifest, bucket=arguments.bucket, prefix=settings.object_store_prefix
    )
    key, sha = publish_release(arguments.root, release, payload, BotoAvailabilityStorage.from_settings())
    return {
        "outcome": "release_staged",
        "release_key": key,
        "release_sha256": sha,
        "shards": len(release.shards),
        "pending_areas": len(release.pending_areas),
        "source_evidence": release.source_evidence,
        "admitted": False,
        "serving_published": False,
    }


def _usage_error(arguments: argparse.Namespace) -> str | None:
    """The one argument rule a command breaks, or None."""
    command = arguments.command
    if command == "capture" and len(arguments.area) != 1:
        return "capture requires exactly one --area per bounded invocation"
    if command == "prepare" and (not arguments.area or arguments.shard is None):
        return "prepare requires --shard and the shard's complete explicit --area list"
    if command in {"verify-replay", "validate", "stage"} and arguments.manifest is None:
        return f"{command} requires --manifest <sha256>"
    if command in {"stage", "release"} and arguments.bucket is None:
        return f"{command} requires --bucket"
    if command == "release" and not arguments.shard_manifest:
        return "release requires at least one --shard-manifest <sha256>"
    return None


def _dry_run_details(arguments: argparse.Namespace) -> dict[str, object]:
    """What a dry run can say from local files alone: validate groups, stage bytes, release shape."""
    if arguments.command == "validate":
        candidate, _ = load_candidate_manifest(arguments.root, arguments.manifest)
        groups = candidate_validation_groups(candidate, arguments.area)
        return {"groups": [list(group) for group in groups], "network_calls": sum(len(group) for group in groups)}
    if arguments.command == "stage":
        plan = stage_plan(arguments.root, arguments.manifest, include_source_evidence=arguments.include_source_evidence)
        journaled = read_journal(
            arguments.root, arguments.manifest, bucket=arguments.bucket, prefix=settings.object_store_prefix
        ).staged_keys
        return {
            "bucket": arguments.bucket,
            "object_count": plan.object_count,
            "byte_count": plan.byte_count,
            "already_journaled": sum(item.key in journaled for item in plan.objects),
            "include_source_evidence": arguments.include_source_evidence,
        }
    if arguments.command == "release":
        release, payload = build_release(
            arguments.root, arguments.shard_manifest, bucket=arguments.bucket, prefix=settings.object_store_prefix
        )
        return {
            "release_sha256": digest(payload),
            "shards": len(release.shards),
            "pending_areas": len(release.pending_areas),
            "source_evidence": release.source_evidence,
        }
    return {}


_APPLY: Final[dict[str, Callable[[argparse.Namespace], dict[str, object]]]] = {
    "areas": lambda arguments: asyncio.run(_areas(arguments)),
    "capture": lambda arguments: asyncio.run(_capture(arguments)),
    "prepare": _prepare,
    "verify-replay": _verify_replay,
    "validate": lambda arguments: asyncio.run(_validate(arguments)),
    "stage": _stage,
    "release": _release,
}


def main(argv: list[str] | None = None) -> None:
    cli = parser()
    # `inline_bbox_value` rewrites `--bbox -125,42,...` to `--bbox=-125,42,...` before argparse ever
    # sees it: a bare leading `-` on the VALUE reads as another flag otherwise, exactly the
    # regression `test_direct_writer_contract.py::test_every_bbox_writer_survives_a_negative_
    # leading_bbox` pins for six sibling writers, and every real bbox this deployment uses (the PNW
    # region envelope, `default_camera_envelope`, an operator override) has a negative west ordinate.
    raw = list(sys.argv[1:]) if argv is None else argv
    arguments = cli.parse_args(inline_bbox_value(raw))
    if (message := _usage_error(arguments)) is not None:
        cli.error(message)
    scope: dict[str, object] = {}
    if arguments.command == "areas":
        # Resolved (and, per SEC-1, echoed) before the dry-run gate, so a misconfigured --bbox is
        # visible immediately rather than only failing once --apply makes a network call.
        bbox, region_slug, overridden = _resolve_scope(arguments)
        scope = {"bbox": list(bbox), "region": region_slug, "region_envelope_override": overridden}
    if not arguments.apply:
        print(
            json.dumps(
                {
                    "outcome": "dry_run",
                    "command": arguments.command,
                    "areas": arguments.area,
                    "root": str(arguments.root),
                    "serving_published": False,
                    **scope,
                    **_dry_run_details(arguments),
                }
            )
        )
        return
    report = _APPLY[arguments.command](arguments)
    print(json.dumps(report, sort_keys=True))
    if report.get("outcome") == "findings":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
