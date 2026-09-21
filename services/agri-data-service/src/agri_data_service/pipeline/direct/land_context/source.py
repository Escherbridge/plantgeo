"""Capture complete bounded BLM query populations with immutable response evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlencode

from agri_data_service.ingest.http import UpstreamBounds, fetch_bounded_text, upstream_client
from agri_data_service.pipeline.direct.land_context.products import (
    ARCHIVE_ROOT,
    PRODUCTS,
    SHA256_HEX_LENGTH,
    ArcgisProduct,
)
from agri_data_service.pipeline.errors import PipelineOperationError

if TYPE_CHECKING:
    import httpx

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage

PAGE_SIZE = 200
MAX_RECORDS = 100_000
MAX_REQUESTS = 600
MAX_CAPTURE_BYTES = 512 * 1024 * 1024
BOUNDS = UpstreamBounds(max_bytes=64 * 1024 * 1024, timeout_seconds=120)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def refuse(message: str) -> PipelineOperationError:
    return PipelineOperationError(message, code="incomplete_source_capture", lane="land-context", stage="source")


@dataclass(frozen=True, slots=True)
class ProductCapture:
    product: ArcgisProduct
    features: tuple[dict[str, Any], ...]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class LandContextSnapshot:
    products: tuple[ProductCapture, ...]
    started_at: datetime
    captured_at: datetime
    manifest_sha256: str
    content_sha256: str


class QuerySession(Protocol):
    async def query(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]: ...


@dataclass(slots=True)
class CaptureSession:
    client: httpx.AsyncClient
    storage: AvailabilityStorage
    deadline: float
    responses: list[dict[str, Any]] = field(default_factory=list)
    transferred: int = 0

    async def query(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        """Archive each successful decoded entity before admitting its parsed content."""
        if len(self.responses) >= MAX_REQUESTS or time.monotonic() >= self.deadline:
            raise refuse("BLM capture exceeded its finite request or time budget")
        url = f"{endpoint}?{urlencode(params)}"
        async with asyncio.timeout(max(0.01, self.deadline - time.monotonic())):
            text = await fetch_bounded_text(self.client, url, BOUNDS)
        body = text.encode("utf-8")
        self.transferred += len(body)
        if self.transferred > MAX_CAPTURE_BYTES:
            raise refuse("BLM capture exceeded its total byte budget")
        identity = digest(body)
        self.storage.put_immutable(f"{ARCHIVE_ROOT}/blobs/{identity}", body, content_type="application/json")
        self.responses.append({"url": endpoint, "params": params, "sha256": identity, "bytes": len(body)})
        parsed = json.loads(body)
        if not isinstance(parsed, dict) or "error" in parsed:
            raise refuse(
                f"ArcGIS refused {endpoint}: {parsed.get('error') if isinstance(parsed, dict) else 'not an object'}"
            )
        if parsed.get("exceededTransferLimit"):
            raise refuse(f"ArcGIS truncated {endpoint}; refusing a partial population")
        return parsed


def object_ids(payload: dict[str, Any]) -> tuple[int, ...]:
    values = payload.get("objectIds")
    if not isinstance(values, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise refuse("ArcGIS object inventory is missing or malformed")
    if len(values) != len(set(values)) or len(values) > MAX_RECORDS:
        raise refuse("ArcGIS object inventory duplicates identifiers or exceeds the record ceiling")
    return tuple(sorted(values))


async def capture_product(session: QuerySession, product: ArcgisProduct) -> ProductCapture:
    """Bracket an ID-batched geometry walk with equal inventories and equal metadata."""
    metadata = await session.query(product.endpoint, {"f": "json"})
    oid_fields = [field["name"] for field in metadata.get("fields", []) if field.get("type") == "esriFieldTypeOID"]
    if len(oid_fields) != 1:
        raise refuse(f"{product.slug} must expose exactly one object-ID field")
    oid_field = oid_fields[0]
    spatial = (
        {
            "geometry": product.envelope,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        }
        if product.envelope
        else {}
    )
    params = {"f": "json", "where": product.where, "returnIdsOnly": "true", **spatial}
    before = object_ids(await session.query(f"{product.endpoint}/query", params))
    count = await session.query(
        f"{product.endpoint}/query", {"f": "json", "where": product.where, "returnCountOnly": "true", **spatial}
    )
    if count.get("count") != len(before) or not before:
        raise refuse(f"{product.slug} count/ID mismatch or unexpected empty source")
    features: list[dict[str, Any]] = []
    native_keys: set[str] = set()
    page_size = 1 if product.envelope else PAGE_SIZE
    for offset in range(0, len(before), page_size):
        ids = before[offset : offset + page_size]
        response = await session.query(
            f"{product.endpoint}/query",
            {
                "f": "geojson",
                "where": product.where,
                "objectIds": ",".join(map(str, ids)),
                "outFields": "*",
                "outSR": "4326",
                "returnGeometry": "true",
                **spatial,
                **({"maxAllowableOffset": "0.0001"} if product.envelope else {}),
            },
        )
        batch = response.get("features")
        if not isinstance(batch, list) or len(batch) != len(ids):
            raise refuse(f"{product.slug} geometry batch omitted requested records")
        returned_ids: list[int] = []
        for feature in batch:
            props = feature.get("properties")
            geometry = feature.get("geometry")
            if not isinstance(props, dict) or not isinstance(geometry, dict):
                raise refuse(f"{product.slug} returned a feature without properties or geometry")
            raw_key = props.get(product.key_field)
            if raw_key is None or not str(raw_key).strip():
                raise refuse(f"{product.slug} returned a feature without its reviewed native key")
            native_key = str(raw_key).strip()
            if native_key in native_keys:
                raise refuse(f"{product.slug} repeated native key {native_key}")
            native_keys.add(native_key)
            object_id = props.get(oid_field)
            if isinstance(object_id, bool) or not isinstance(object_id, int):
                raise refuse(f"{product.slug} returned a feature without an integer object ID")
            returned_ids.append(object_id)
            features.append(feature)
        if sorted(returned_ids) != list(ids):
            raise refuse(f"{product.slug} response identifiers differ from the exact requested batch")
    after = object_ids(await session.query(f"{product.endpoint}/query", params))
    final_metadata = await session.query(product.endpoint, {"f": "json"})
    if before != after or metadata != final_metadata:
        raise refuse(f"{product.slug} changed inventory or metadata during capture")
    ordered = tuple(sorted(features, key=lambda feature: str(feature["properties"][product.key_field])))
    return ProductCapture(product=product, features=ordered, content_sha256=digest(canonical_bytes(ordered)))


async def fetch_snapshot(storage: AvailabilityStorage, *, timeout_seconds: float = 1800) -> LandContextSnapshot:
    """Fetch the admitted products once and durably bind their capture interval and hashes."""
    started = datetime.now(UTC)
    async with upstream_client(BOUNDS) as client:
        session = CaptureSession(client=client, storage=storage, deadline=time.monotonic() + timeout_seconds)
        products = tuple([await capture_product(session, product) for product in PRODUCTS])
    captured = datetime.now(UTC)
    content_identity = digest(canonical_bytes({capture.product.slug: capture.content_sha256 for capture in products}))
    manifest = {
        "schema": "blm-pnw-current-capture/v1",
        "started_at": started.isoformat(),
        "captured_at": captured.isoformat(),
        "source_content_sha256": content_identity,
        "responses": session.responses,
        "products": [
            {
                "slug": capture.product.slug,
                "endpoint": capture.product.endpoint,
                "where": capture.product.where,
                "count": len(capture.features),
                "sha256": capture.content_sha256,
            }
            for capture in products
        ],
        "consistency": (
            "equal before/after object inventories and metadata; mutable source, not an atomic historical release"
        ),
    }
    body = canonical_bytes(manifest)
    identity = digest(body)
    storage.put_immutable(f"{ARCHIVE_ROOT}/manifests/{identity}.json", body, content_type="application/json")
    return LandContextSnapshot(products, started, captured, identity, content_identity)


@dataclass(slots=True)
class ReplaySession:
    storage: AvailabilityStorage
    responses: list[dict[str, Any]]
    position: int = 0

    async def query(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        if self.position >= len(self.responses):
            raise refuse("Archived BLM response graph is incomplete")
        receipt = self.responses[self.position]
        self.position += 1
        if receipt["url"] != endpoint or receipt["params"] != params:
            raise refuse("Archived BLM query differs from the admitted source definition")
        if not isinstance(receipt["bytes"], int) or not 0 < receipt["bytes"] <= BOUNDS.max_bytes:
            raise refuse("Archived BLM response byte budget is invalid")
        stored = self.storage.read(f"{ARCHIVE_ROOT}/blobs/{receipt['sha256']}", max_bytes=receipt["bytes"])
        if stored is None or digest(stored.payload) != receipt["sha256"] or len(stored.payload) != receipt["bytes"]:
            raise refuse("Archived BLM response hash/size verification failed")
        parsed = json.loads(stored.payload)
        if not isinstance(parsed, dict) or "error" in parsed or parsed.get("exceededTransferLimit"):
            raise refuse("Archived BLM source response is refused or truncated")
        return parsed


async def replay_snapshot(storage: AvailabilityStorage, identity: str) -> LandContextSnapshot:
    """Reconstruct a partial publication from its original immutable response graph."""
    if len(identity) != SHA256_HEX_LENGTH or any(character not in "0123456789abcdef" for character in identity):
        raise refuse("Malformed BLM manifest identity")
    stored = storage.read(f"{ARCHIVE_ROOT}/manifests/{identity}.json", max_bytes=2 * 1024 * 1024)
    if stored is None or digest(stored.payload) != identity:
        raise refuse("BLM source manifest is missing or fails its hash")
    manifest = json.loads(stored.payload)
    responses = manifest.get("responses")
    if (
        manifest.get("schema") != "blm-pnw-current-capture/v1"
        or not isinstance(responses, list)
        or len(responses) > MAX_REQUESTS
    ):
        raise refuse("BLM source manifest is not an admitted bounded capture")
    session = ReplaySession(storage, responses)
    captures = tuple([await capture_product(session, product) for product in PRODUCTS])
    content_identity = digest(canonical_bytes({capture.product.slug: capture.content_sha256 for capture in captures}))
    if session.position != len(responses) or content_identity != manifest["source_content_sha256"]:
        raise refuse("BLM archive replay does not reproduce its whole source-content claim")
    started = datetime.fromisoformat(manifest["started_at"])
    captured = datetime.fromisoformat(manifest["captured_at"])
    if started.tzinfo is None or captured.tzinfo is None or started > captured:
        raise refuse("BLM archive capture interval is invalid")
    return LandContextSnapshot(captures, started, captured, identity, content_identity)
