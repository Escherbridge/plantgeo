"""Offline SSURGO acquisition commands: region-wide area census, then bounded per-area capture.

Ported and narrowed from `archive/freshness-integrated-candidate-20260914`'s `pipeline/direct/
soil_survey/__main__.py` for slice S1 ("Acquisition"): only `areas` and `capture` are wired here.
`prepare`, `verify-replay`, `validate`, `stage` and `release` are a later slice's sequenced edit to
this same file (`AGENTS.md`, "Operational entry point"). Every command is dry-run unless `--apply`
is given, and neither command here writes to an object store, advances admission, or reports
`serving_published=true` -- this package is not registered as a scheduled lane
(`pipeline/parquet/lane_registry.py` still refuses it) and nothing calls it from a cron.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import ValidationError

from agri_data_service.foundation.geography.bounding_box import BoundingBox, inline_bbox_value, parse_bounding_box
from agri_data_service.foundation.region import load_region
from agri_data_service.foundation.soil_survey.receipts import (
    AreaCensusEntry,
    AreaInventory,
    SoilSurveyError,
    digest,
)
from agri_data_service.pipeline.direct.soil_survey.capture import capture_area, save_blob, save_checkpoint
from agri_data_service.pipeline.direct.soil_survey.source import (
    AREA_INVENTORY_COLUMNS,
    area_inventory_query,
    fetch,
    table_rows,
)


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


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("areas", "capture"))
    result.add_argument("--root", type=Path, required=True)
    result.add_argument("--area", action="append", default=[])
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
    result.add_argument("--max-pages", type=int, default=8)
    result.add_argument("--page-size", type=int, default=500)
    result.add_argument("--time-budget-seconds", type=float, default=120)
    result.add_argument(
        "--apply", action="store_true", help="Perform the named action; neither command ever admits a release"
    )
    return result


async def _areas(arguments: argparse.Namespace) -> dict[str, object]:
    bbox, region_slug, overridden = _resolve_scope(arguments)
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
    async with httpx.AsyncClient(follow_redirects=False) as client:
        query = area_inventory_query(bbox)
        payload = await fetch(client, query)
        blob = save_blob(arguments.root, payload)
        rows = table_rows(payload, AREA_INVENTORY_COLUMNS)
    entries: list[AreaCensusEntry] = []
    for census_row in rows:
        area_symbol, saverest = census_row["areasymbol"], census_row["saverest"]
        if area_symbol is None or saverest is None:
            # P1-02: a NULL symbol or vintage is refused, not silently dropped -- the saved
            # AreaInventory must always match the response blob it cites (AGENTS.md, "Bounds,
            # resume and missingness"). Non-SSURGO STATSGO2 rows are excluded upstream in
            # ssurgo_area_inventory.sql, not filtered here.
            raise SoilSurveyError(f"area-inventory row is missing its symbol or vintage: {census_row!r}")
        try:
            entries.append(AreaCensusEntry(area=area_symbol, saverest=saverest))
        except ValidationError as error:
            # Belt-and-suspenders alongside the SQL-side LEN(sac.areasymbol) = 5 filter: a symbol or
            # vintage that still does not fit the receipt's own shape is a refusal, not an uncaught
            # pydantic ValidationError.
            raise SoilSurveyError(f"area-inventory row failed validation: {census_row!r}: {error}") from error
    if not entries:
        # P1-02/P1-06: an empty area-inventory response is refused outright; it is not a governed
        # absence and must never be recorded as `outcome: censused`.
        raise SoilSurveyError("area-inventory census returned zero areas; this is not a governed absence")
    try:
        inventory = AreaInventory(
            response=blob,
            query_sha256=digest(query.encode()),
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
            time_budget_seconds=arguments.time_budget_seconds,
        )
    return {
        "outcome": "captured" if capture.closing is not None else "incomplete",
        "area": capture.opening.area,
        "rows": sum(page.row_count for page in capture.pages),
        "expected_rows": capture.opening.count,
        "serving_published": False,
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
    if arguments.command == "capture" and len(arguments.area) != 1:
        cli.error("capture requires exactly one --area per bounded invocation")
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
                }
            )
        )
        return
    report = asyncio.run(_areas(arguments)) if arguments.command == "areas" else asyncio.run(_capture(arguments))
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
