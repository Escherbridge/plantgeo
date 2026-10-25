"""`land_context.blm_arcgis`: object-inventory request planning over the three admitted BLM ArcGIS
services (`products.py`). Strategy key `land_context.blm_arcgis` (spec S14, §7 land-context row).

CLASS E SHADOW (spec.md §4.8.1, O7): `lanes/land-context-blm-{forward,reconcile,backfill}.toml` name
this module with `executor = "legacy"`, so the runner never actually dispatches it
(`pipeline/runner/AGENTS.md` "Resolution"; `execution/lane_catalogue.py`). `plan_requests` and `fetch`
are REAL: they ask the same three ArcGIS services `pipeline/direct/land_context/source.py
::capture_product` does, for the same object-ID inventory, with the same refusal shape, so a future
activation reviews working request planning instead of authoring it from zero. `settle` and `rows`
are DELIBERATELY NOT IMPLEMENTED: which Parquet stream each service's answer becomes, how
reconcile/backfill map onto the runner's `forward`/`gap-fill` turn modes, and how an object-ID
inventory becomes a written `land-context-boundaries`/`-offices`/`-contacts` day are exactly the
"reconcile mapping decided at activation" this file defers (spec.md §4.8.1, O7; class E's clock start
is "never" inside `config_driven_ingestion_20260926`) -- guessing that mapping now would ship
unreviewed semantics nobody asked for.

TIGERweb residual: `products.py::PRODUCTS` also admits `CENSUS_STATES`, a census-state-context query
against `tigerweb.geo.census.gov` -- a DIFFERENT publisher with no provider file
(`lanes/_providers/blm.toml` says so explicitly in its own header). `PLANNED_PRODUCTS` below omits it
on purpose; that query stays on the legacy `pipeline/direct/land_context` path.

Imports only `pipeline/runner/contract.py` (S14), `pipeline/lanes/_shared` (the envelope verdict and
digest helpers `source.py` uses too) and this package's own `products.py`; never
`pipeline/errors.py` (`pipeline/lanes/land_context/AGENTS.md` names it "a residual the strategy
should drop") and never a local scratch directory -- every answer flows through `SourceRequest` /
`SourceResponse` and the runner's own ports, never a temp file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.pipeline.lanes._shared.arcgis import ArcgisEnvelopeFault, arcgis_envelope_fault
from agri_data_service.pipeline.lanes._shared.digest import sha256_hex
from agri_data_service.pipeline.lanes.land_context.products import FIELD_OFFICES, SURFACE_IDAHO, SURFACE_ORWA
from agri_data_service.pipeline.runner.contract import SourceRequest, SourceResponse

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.lanes.land_context.products import ArcgisProduct
    from agri_data_service.pipeline.runner.contract import DayContext, ProviderClient, Settlement

#: The three BLM ArcGIS services this strategy plans against (`SURFACE_ORWA`, `SURFACE_IDAHO`,
#: `FIELD_OFFICES`) -- `products.py::CENSUS_STATES` (TIGERweb) is deliberately absent; see module docstring.
PLANNED_PRODUCTS: Final[tuple[ArcgisProduct, ...]] = (SURFACE_ORWA, SURFACE_IDAHO, FIELD_OFFICES)

#: Which `lanes/_providers/blm.toml` endpoint answers each product's object-ID inventory ask.
_PROVIDER_QUERY_ENDPOINT: Final[dict[str, str]] = {
    SURFACE_ORWA.slug: "or-ownership-query",
    SURFACE_IDAHO.slug: "national-sma-query",
    FIELD_OFFICES.slug: "field-offices-query",
}

#: `requested_parameters` / `complete_parameters` name one value: there is no second field an ArcGIS
#: object-ID inventory answers partially -- the whole ask settles, or it does not.
OBJECT_INVENTORY_PARAMETER: Final = "object_inventory"

#: The upper bound `source.py::object_ids` also enforces; kept local so this module never imports
#: `pipeline/errors.py` (see module docstring) to raise the same shape of refusal.
_MAX_RECORDS: Final = 100_000


def _inventory_parameters(product: ArcgisProduct) -> tuple[tuple[str, str], ...]:
    """The same object-ID inventory ask `source.py::capture_product`'s first call builds, sorted."""
    parameters: dict[str, str] = {"f": "json", "where": product.where, "returnIdsOnly": "true"}
    if product.envelope is not None:
        parameters.update(
            {
                "geometry": product.envelope,
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
            }
        )
    return tuple(sorted(parameters.items()))


def _support_digest(product: ArcgisProduct) -> str:
    """Binds a checkpoint to the exact filter it was planned over (`SourceRequest.support_digest`)."""
    binds = f"{product.slug}:{product.where}:{product.envelope or ''}"
    return sha256_hex(binds.encode("utf-8"))


def _object_ids(body: bytes, *, unit: str, endpoint: str) -> tuple[int, ...]:
    """Validate one service's object-ID inventory: readable JSON, no refusal, no truncation, a clean
    bounded ID set -- the same FOUR refusal shapes `source.py` checks, across TWO of its functions.

    `CaptureSession.query` (`source.py:90-96`) checks JSON-readability, `"error"` and
    `exceededTransferLimit` BEFORE `object_ids()` ever sees the payload; `object_ids` (`source.py:99-105`)
    then checks the list shape, duplicate identifiers and the record ceiling. This leaf restates all
    four rather than importing either function, because both raise through `pipeline/errors.py`, which
    this strategy leaf does not import (module docstring). Missing the `exceededTransferLimit` check
    would accept a server-truncated, partial inventory as a complete population -- exactly the gap
    `source.py` closes unconditionally before `object_ids()` runs at all.
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError(f"{unit}: ArcGIS object-inventory response is not valid JSON") from error
    fault = arcgis_envelope_fault(parsed)
    if fault in {ArcgisEnvelopeFault.NOT_AN_OBJECT, ArcgisEnvelopeFault.SERVICE_ERROR}:
        raise ValueError(f"{unit}: ArcGIS refused the object-inventory ask")
    if fault is ArcgisEnvelopeFault.TRUNCATED:
        raise ValueError(f"{unit}: ArcGIS truncated {endpoint}; refusing a partial population")
    values = parsed.get("objectIds")
    if not isinstance(values, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ValueError(f"{unit}: ArcGIS object inventory is missing or malformed")
    if len(values) != len(set(values)) or len(values) > _MAX_RECORDS:
        raise ValueError(f"{unit}: ArcGIS object inventory duplicates identifiers or exceeds the record ceiling")
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class BlmArcgisStrategy:
    """Object-inventory units over the three admitted BLM ArcGIS services; see module docstring."""

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:
        """One unit per admitted service: the BLM services carry their own fixed filter (`products.py`),
        never a region lattice, so `lane` and `region` are unused on purpose."""
        del lane, region
        asked = tuple(sorted(set(days)))
        return [
            SourceRequest(
                unit=product.slug,
                endpoint=_PROVIDER_QUERY_ENDPOINT[product.slug],
                days=asked,
                parameters=_inventory_parameters(product),
                requested_parameters=frozenset({OBJECT_INVENTORY_PARAMETER}),
                support_digest=_support_digest(product),
            )
            for product in PLANNED_PRODUCTS
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """ONE send per service: `_object_ids` applies the same four refusal shapes `source.py` does,
        across `CaptureSession.query` and `object_ids` (see that function's docstring)."""
        response = await client.get(request.endpoint, request.query())
        inventory = _object_ids(response.body, unit=request.unit, endpoint=request.endpoint)
        return SourceResponse(
            request=request,
            body=response.body,
            retrieved_at=response.retrieved_at,
            complete_parameters=request.requested_parameters,
            payload=inventory,
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:
        """NOT IMPLEMENTED: activation decides stream ownership and the forward/gap-fill mapping (O7)."""
        del day, responses, context
        raise NotImplementedError(
            "land_context.blm_arcgis: settle() is deferred to activation (spec.md section 4.8.1, O7); "
            "plan_requests and fetch() plan and answer the three admitted ArcGIS services' object-ID "
            "inventories, but how an inventory becomes a written land-context-boundaries/-offices/-contacts "
            "day, and how reconcile/backfill map onto the runner's forward/gap-fill turn modes, is not "
            "yet decided."
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table | Mapping[str, pa.Table]:
        """NOT IMPLEMENTED: see `settle()`."""
        del day, responses
        raise NotImplementedError("land_context.blm_arcgis: rows() is deferred to activation; see settle().")


STRATEGY: Final = BlmArcgisStrategy()

__all__ = [
    "OBJECT_INVENTORY_PARAMETER",
    "PLANNED_PRODUCTS",
    "STRATEGY",
    "BlmArcgisStrategy",
]
