"""Fail-closed custom storage binding; see this directory's AGENTS.md."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol, cast

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from agri_data_service.foundation.region.identity import (
    IDENTITY_VERSION,
    canonical_token,
    geography_definition,
    geography_digest,
    is_custom_region,
)
from agri_data_service.foundation.region.manifest import load_region

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.foundation.region.manifest import Region

NAMESPACE_RECEIPT = "_region_namespace.json"
MAX_RECEIPT_BYTES = 16384
_TRANSPORT_FAULTS = (BotoCoreError, OSError, ValueError)


class RegionAdmissionError(ValueError):
    """Sanitized custom-region admission refusal."""


class NamespaceClient(Protocol):
    """Minimal conditional namespace storage boundary."""

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def put_object(self, **kwargs: object) -> object: ...


class ReceiptBody(Protocol):
    """Bounded streaming response used only for namespace receipts."""

    def read(self, size: int) -> object: ...

    def close(self) -> None: ...


def namespace_prefix(region: Region) -> str:
    """Canonical custom namespace, without mutable history or layer selection."""
    return f"regions/v1/{geography_digest(region)}"


def namespace_receipt(region: Region) -> bytes:
    """Render the exact immutable namespace claim; this is not source admission."""
    payload = json.dumps(
        {
            "identity_version": IDENTITY_VERSION,
            "geography_sha256": geography_digest(region),
            "geography_canonical_hex": canonical_token(geography_definition(region)).hex(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    if len(payload) > MAX_RECEIPT_BYTES:
        raise RegionAdmissionError("region_namespace_receipt_oversized")
    return payload


def verify_namespace(client: NamespaceClient, bucket: str, prefix: str, region: Region) -> None:
    """Read only the bounded namespace receipt, before any custom data operation."""
    if not is_custom_region(region):
        return
    if prefix.rstrip("/") != namespace_prefix(region):
        raise RegionAdmissionError("region_namespace_prefix_mismatch")
    expected = namespace_receipt(region)
    try:
        response = client.get_object(Bucket=bucket, Key=f"{prefix.rstrip('/')}/{NAMESPACE_RECEIPT}")
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        reason = (
            "region_namespace_receipt_missing"
            if code in ("NoSuchKey", "404", "NotFound")
            else "region_namespace_unavailable"
        )
        raise RegionAdmissionError(reason) from None
    except _TRANSPORT_FAULTS:
        raise RegionAdmissionError("region_namespace_unavailable") from None
    _verify_receipt_body(response.get("Body"), expected)


def _verify_receipt_body(body: object, expected: bytes) -> None:
    """Close the bounded stream while preserving the primary sanitized failure."""
    failure: RegionAdmissionError | None = None
    try:
        if body is None or not hasattr(body, "read"):
            raise RegionAdmissionError("region_namespace_receipt_invalid")
        payload = cast("ReceiptBody", body).read(MAX_RECEIPT_BYTES + 1)
        if not isinstance(payload, bytes) or len(payload) > MAX_RECEIPT_BYTES:
            raise RegionAdmissionError("region_namespace_receipt_invalid")
        if payload != expected:
            raise RegionAdmissionError("region_namespace_receipt_mismatch")
    except RegionAdmissionError as error:
        failure = error
    except _TRANSPORT_FAULTS:
        failure = RegionAdmissionError("region_namespace_unavailable")
    finally:
        if body is not None and hasattr(body, "close"):
            try:
                cast("ReceiptBody", body).close()
            except _TRANSPORT_FAULTS:
                if failure is None:
                    failure = RegionAdmissionError("region_namespace_unavailable")
    if failure is not None:
        raise failure from None


def create_namespace_receipt(client: NamespaceClient, bucket: str, prefix: str, region: Region) -> None:
    """Explicit conditional namespace reservation; no observation or source admission is granted."""
    if not is_custom_region(region) or prefix != namespace_prefix(region):
        raise RegionAdmissionError("region_namespace_prefix_mismatch")
    try:
        client.put_object(
            Bucket=bucket,
            Key=f"{prefix}/{NAMESPACE_RECEIPT}",
            Body=namespace_receipt(region),
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in ("PreconditionFailed", "412"):
            raise RegionAdmissionError("region_namespace_create_failed") from None
    verify_namespace(client, bucket, prefix, region)


def guard_custom_operation(client: NamespaceClient, bucket: str, key: str) -> None:
    """Verify namespace then refuse data access until source support admission is implemented."""
    region = load_region()
    if not is_custom_region(region):
        return
    prefix = namespace_prefix(region)
    if not key.startswith(prefix + "/"):
        raise RegionAdmissionError("region_namespace_prefix_mismatch")
    verify_namespace(client, bucket, prefix, region)
    raise RegionAdmissionError("region_source_support_not_admitted")
