"""Versioned region identities; canonical encoding is specified in this directory's AGENTS.md."""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agri_data_service.foundation.region.manifest import Region

IDENTITY_VERSION = "plantgeo-region-v1"
_SAFE_INTEGER = 9007199254740991


def canonical_token(value: object) -> bytes:
    """Encode JSON-shaped values without language-dependent numeric representations."""
    if value is None:
        return b"n"
    if isinstance(value, bool):
        return b"t" if value else b"f"
    if isinstance(value, (int, float)):
        if isinstance(value, int) and abs(value) > _SAFE_INTEGER:
            raise ValueError("identity_number_out_of_range")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("identity_number_not_finite")
        if number.is_integer() and abs(number) > _SAFE_INTEGER:
            raise ValueError("identity_number_out_of_range")
        return b"d" + struct.pack(">d", 0.0 if number == 0 else number).hex().encode("ascii")
    if isinstance(value, str):
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeError:
            raise ValueError("identity_unicode_invalid") from None
        return b"s" + str(len(encoded)).encode("ascii") + b":" + encoded
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("identity_object_key_invalid")
        keys = sorted(value, key=lambda key: canonical_token(key).split(b":", 1)[1])
        return (
            b"m"
            + str(len(keys)).encode("ascii")
            + b":"
            + b"".join(canonical_token(key) + canonical_token(value[key]) for key in keys)
        )
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return b"a" + str(len(value)).encode("ascii") + b":" + b"".join(map(canonical_token, value))
    raise ValueError("identity_value_invalid")


def identity_digest(domain: str, value: object) -> str:
    """SHA-256 over a versioned, domain-separated canonical value."""
    return hashlib.sha256(canonical_token([IDENTITY_VERSION, domain, value])).hexdigest()


def geography_definition(region: Region) -> dict[str, object]:
    """Select geographic semantics independently of layer selection and analysis support."""
    return {
        "envelope": region.envelope.model_dump(),
        "default_camera_envelope": region.default_camera_envelope.model_dump(),
        "sub_envelopes": {name: box.model_dump() for name, box in region.sub_envelopes.items()},
        "crs": region.crs,
        "timezone": region.timezone,
        "iso_country_codes": sorted(region.iso_country_codes),
        "admin_codes": sorted(region.admin_codes),
    }


def geography_digest(region: Region) -> str:
    """Identify a physical deployment independently of labels and selected layers."""
    return identity_digest("geography", geography_definition(region))


def definition_digest(region: Region) -> str:
    """Identify manifest behavior for runtime agreement, without granting source admission."""
    return identity_digest(
        "definition",
        {
            "geography_sha256": geography_digest(region),
            "slug": region.slug,
            "lattice_pitch_degrees": region.lattice_pitch_degrees,
            "lattice_origin_rule": region.lattice_origin_rule,
            "platform_layers": sorted(region.platform_layers),
            "enabled_layers": [
                binding.model_dump() for binding in sorted(region.enabled_layers, key=lambda item: item.layer_slug)
            ],
            "analysis_lattices": {name: lattice.model_dump() for name, lattice in region.analysis_lattices.items()},
        },
    )


@dataclass(frozen=True, slots=True)
class StreamSupportDescriptor:
    """Explicit source contract and actual support, supplied by an admitted source compiler."""

    layer: str
    source: str
    product: str
    source_contract_version: str
    schema_version: str
    support: Mapping[str, object]

    def digest(self, region: Region) -> str:
        """Bind support to its source without depending on unrelated selected layers."""
        fields = (self.layer, self.source, self.product, self.source_contract_version, self.schema_version)
        if any(not field or field != field.strip() for field in fields) or not self.support:
            raise ValueError("stream_support_descriptor_incomplete")
        if not any(
            binding.layer_slug == self.layer and binding.source_slug == self.source for binding in region.enabled_layers
        ):
            raise ValueError("stream_support_binding_mismatch")
        return identity_digest(
            "stream-support",
            {
                "geography_sha256": geography_digest(region),
                "layer": self.layer,
                "source": self.source,
                "product": self.product,
                "source_contract_version": self.source_contract_version,
                "schema_version": self.schema_version,
                "support": self.support,
            },
        )


def is_custom_region(region: Region) -> bool:
    """Whether the manifest needs custom admission rather than packaged compatibility."""
    return region.slug not in ("pnw", "kenya-highlands")
