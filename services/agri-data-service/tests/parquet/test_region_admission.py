"""Namespace verification never grants custom source admission or reads observation data."""

import io
import json
import traceback
from importlib import resources

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError  # type: ignore[import-untyped]

from agri_data_service.foundation.region.manifest import Region
from agri_data_service.pipeline.parquet.region_admission import (
    NAMESPACE_RECEIPT,
    RegionAdmissionError,
    guard_custom_operation,
    namespace_prefix,
    namespace_receipt,
    verify_namespace,
)


class ReceiptClient:
    def __init__(self, payload: bytes | None) -> None:
        self.payload = payload
        self.keys: list[str] = []

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.keys.append(str(kwargs["Key"]))
        if self.payload is None:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.payload)}

    def put_object(self, **_kwargs: object) -> object:
        pytest.fail("verification must never create a receipt")


def _region() -> Region:
    return Region.model_validate_json(
        resources.files("agri_data_service.foundation.region").joinpath("custom_colorado.fixture.json").read_text()
    )


@pytest.mark.parametrize(("payload", "reason"), [(None, "missing"), (b"{}", "mismatch"), (b"x" * 16385, "invalid")])
def test_namespace_refuses_missing_wrong_and_oversized_receipts(payload: bytes | None, reason: str) -> None:
    region = _region()
    client = ReceiptClient(payload)
    with pytest.raises(RegionAdmissionError, match=f"region_namespace_receipt_{reason}"):
        verify_namespace(client, "bucket", namespace_prefix(region), region)
    assert client.keys == [f"{namespace_prefix(region)}/{NAMESPACE_RECEIPT}"]


def test_wrong_prefix_refuses_before_any_storage_access() -> None:
    client = ReceiptClient(None)
    with pytest.raises(RegionAdmissionError, match="prefix_mismatch"):
        verify_namespace(client, "bucket", "old-region", _region())
    assert client.keys == []


def test_receipt_does_not_authorize_source_or_observation_read(monkeypatch: pytest.MonkeyPatch) -> None:
    region = _region()
    monkeypatch.setenv("PLANTGEO_REGION", region.slug)
    fixture = (
        resources.files("agri_data_service.foundation.region").joinpath("custom_colorado.fixture.json").read_text()
    )
    monkeypatch.setenv("PLANTGEO_REGION_MANIFEST_JSON", fixture)
    client = ReceiptClient(namespace_receipt(region))
    with pytest.raises(RegionAdmissionError, match="region_source_support_not_admitted"):
        guard_custom_operation(client, "bucket", f"{namespace_prefix(region)}/layer=weather/data.parquet")
    assert client.keys == [f"{namespace_prefix(region)}/{NAMESPACE_RECEIPT}"]


def test_same_slug_new_geography_cannot_reuse_receipt() -> None:
    region = _region()
    document = json.loads(
        resources.files("agri_data_service.foundation.region").joinpath("custom_colorado.fixture.json").read_text()
    )
    document["timezone"] = "America/Chicago"
    changed = Region.model_validate(document)
    assert changed.slug == region.slug
    client = ReceiptClient(namespace_receipt(region))
    with pytest.raises(RegionAdmissionError, match="receipt_mismatch"):
        verify_namespace(client, "bucket", namespace_prefix(changed), changed)


def test_equivalent_numeric_geography_reuses_receipt() -> None:
    region = _region()
    positive = region.model_copy(update={"sub_envelopes": {"zero": region.envelope.model_copy(update={"south": 0.0})}})
    negative = positive.model_copy(
        update={"sub_envelopes": {"zero": region.envelope.model_copy(update={"south": -0.0})}}
    )
    assert namespace_prefix(positive) == namespace_prefix(negative)
    assert namespace_receipt(positive) == namespace_receipt(negative)


def test_receipt_size_is_bounded_before_publication() -> None:
    region = _region()
    large = region.model_copy(update={"sub_envelopes": {f"area-{index}": region.envelope for index in range(1000)}})
    with pytest.raises(RegionAdmissionError, match="receipt_oversized"):
        namespace_receipt(large)


@pytest.mark.parametrize("phase", ["get", "read", "close"])
@pytest.mark.parametrize("transport", ["botocore", "os"])
def test_namespace_transport_faults_are_sanitized(phase: str, transport: str) -> None:
    region = _region()
    secret = "sensitive-upstream-context"
    fault = EndpointConnectionError(endpoint_url=secret) if transport == "botocore" else OSError(secret)
    operations: list[str] = []

    class Body:
        def read(self, _size: int) -> bytes:
            operations.append("read")
            if phase == "read":
                raise fault
            return namespace_receipt(region)

        def close(self) -> None:
            operations.append("close")
            if phase == "close":
                raise fault

    class Client(ReceiptClient):
        def get_object(self, **kwargs: object) -> dict[str, object]:
            operations.append(str(kwargs["Key"]))
            if phase == "get":
                raise fault
            return {"Body": Body()}

    with pytest.raises(RegionAdmissionError, match=r"^region_namespace_unavailable$") as captured:
        verify_namespace(Client(None), "bucket", namespace_prefix(region), region)
    assert secret not in "".join(traceback.format_exception(captured.value))
    receipt_key = f"{namespace_prefix(region)}/{NAMESPACE_RECEIPT}"
    assert operations == ([receipt_key] if phase == "get" else [receipt_key, "read", "close"])


def test_cleanup_fault_does_not_replace_primary_mismatch() -> None:
    region = _region()

    class Body:
        def read(self, _size: int) -> bytes:
            return b"wrong receipt"

        def close(self) -> None:
            raise OSError("sensitive-cleanup-context")

    class Client(ReceiptClient):
        def get_object(self, **_kwargs: object) -> dict[str, object]:
            return {"Body": Body()}

    with pytest.raises(RegionAdmissionError, match=r"^region_namespace_receipt_mismatch$") as captured:
        verify_namespace(Client(None), "bucket", namespace_prefix(region), region)
    assert "sensitive-cleanup-context" not in "".join(traceback.format_exception(captured.value))
