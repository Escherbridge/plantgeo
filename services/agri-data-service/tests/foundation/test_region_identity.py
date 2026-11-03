"""Cross-runtime canonical protocol and independent geographic/support lifetimes."""

import json
from importlib import resources
from typing import TypedDict, cast

import pytest

from agri_data_service.foundation.region.identity import (
    StreamSupportDescriptor,
    canonical_token,
    definition_digest,
    geography_digest,
    identity_digest,
)
from agri_data_service.foundation.region.manifest import LayerBinding, Region


class IdentityVector(TypedDict):
    value: object
    token_hex: str
    sha256: str


class IdentityFixture(TypedDict):
    domain: str
    vectors: list[IdentityVector]
    manifest: dict[str, object]
    geography_sha256: str
    definition_sha256: str


def _fixture() -> IdentityFixture:
    return cast(
        "IdentityFixture",
        json.loads(
            resources.files("agri_data_service.foundation.region").joinpath("identity_vectors.fixture.json").read_text()
        ),
    )


def test_canonical_cross_runtime_vectors() -> None:
    fixture = _fixture()
    for vector in fixture["vectors"]:
        assert canonical_token(vector["value"]).hex() == vector["token_hex"]
        assert identity_digest(fixture["domain"], vector["value"]) == vector["sha256"]
    region = Region.model_validate(fixture["manifest"])
    assert geography_digest(region) == fixture["geography_sha256"]
    assert definition_digest(region) == fixture["definition_sha256"]


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 2**53, float(2**53), 1e20, "\ud800", {"\ud800": 1}])
def test_unsupported_values_fail_without_echoing_input(value: object) -> None:
    with pytest.raises(ValueError, match=r"^identity_[a-z_]+$"):
        canonical_token(value)


def test_identity_lifetimes_are_independent() -> None:
    region = Region.model_validate(_fixture()["manifest"])
    binding = region.enabled_layers[0]
    descriptor = StreamSupportDescriptor(
        binding.layer_slug, binding.source_slug, "daily", "1", "1", {"points": [[-107.5, 39.5]]}
    )
    labelled = region.model_copy(update={"display_name": "Different presentation"})
    assert definition_digest(labelled) == definition_digest(region)
    another = next(
        layer for layer in region.platform_layers if layer not in {item.layer_slug for item in region.enabled_layers}
    )
    expanded = region.model_copy(
        update={
            "enabled_layers": (
                *region.enabled_layers,
                LayerBinding(layer_slug=another, source_slug="example", coverage="regional"),
            )
        }
    )
    assert geography_digest(expanded) == geography_digest(region)
    assert definition_digest(expanded) != definition_digest(region)
    assert descriptor.digest(expanded) == descriptor.digest(region)
    changed = StreamSupportDescriptor(binding.layer_slug, binding.source_slug, "daily", "2", "1", descriptor.support)
    assert changed.digest(region) != descriptor.digest(region)


def test_membership_order_does_not_change_identity() -> None:
    region = Region.model_validate(_fixture()["manifest"])
    reordered = region.model_copy(
        update={
            "platform_layers": tuple(reversed(region.platform_layers)),
            "enabled_layers": tuple(reversed(region.enabled_layers)),
        }
    )
    assert definition_digest(reordered) == definition_digest(region)
