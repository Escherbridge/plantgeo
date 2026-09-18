"""The boot-time check that every layer binding in a region manifest is actually servable.

See `AGENTS.md` in this directory, section "Source coverage claims", for the rationale and for why
a binding whose source has not declared a claim yet is reported rather than silently accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.foundation.region.manifest import LayerBinding, Region
    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim


class RegionBindingNotServableError(RuntimeError):
    """Raised at startup when a manifest binds a layer to a source that cannot serve the region."""


@dataclass(frozen=True, slots=True)
class LayerSourceContracts:
    """Which `runtime_checkable` Protocol each layer expects, and every source instance by slug.

    Passed IN from `pipeline/source_bindings.py::declared_layer_source_contracts()` exactly as
    `source_claims` is, because `foundation` may not import `pipeline`
    (`tests/test_layer_import_contract.py`) and the protocol classes live beside their lanes.

    The two maps are keyed differently ON PURPOSE. A cross-layer mis-binding is exactly the case
    where the bound source is registered under some OTHER layer, so the instance has to be
    reachable by slug alone -- a per-layer implementation map would simply not find `ssurgo` under
    `drought` and would report nothing (STYLE-REVIEW-W5 B2).
    """

    #: One layer slug to the `typing.Protocol` it expects, which MUST be `runtime_checkable`.
    protocol_by_layer: Mapping[str, type]
    #: `{source_slug: instance}` for every registered implementation, whatever layer it serves.
    source_by_slug: Mapping[str, object]


def unverified_binding_slugs(
    region: Region,
    source_claims: Mapping[str, SourceCoverageClaim],
) -> tuple[str, ...]:
    """The bound source slugs with no declared coverage claim yet, sorted -- the remaining protocol debt.

    `federation.md` §5 introduces the per-layer source protocol three layers at a time, so most of a
    manifest's bindings carry only the manifest's own `coverage` word and no source-side claim to
    check it against. Naming them is the honest reading of "not looked at", and is deliberately not
    an error: failing boot for a layer whose protocol has simply not landed yet would make the
    migration undeployable in the middle.
    """
    return tuple(
        sorted({binding.source_slug for binding in region.enabled_layers if binding.source_slug not in source_claims})
    )


def _protocol_conformance_failures(
    binding: LayerBinding,
    contracts: LayerSourceContracts | None,
) -> tuple[str, ...]:
    """The one-line refusal for a source bound to a layer whose protocol it does not implement.

    Silent for a layer with no declared protocol and for a source slug this build registers no
    implementation of: the first is the protocol migration mid-flight, and the second is already
    reported by `unverified_binding_slugs` (no implementation means no coverage claim either).
    """
    if contracts is None:
        return ()
    protocol = contracts.protocol_by_layer.get(binding.layer_slug)
    source = contracts.source_by_slug.get(binding.source_slug)
    if protocol is None or source is None or isinstance(source, protocol):
        return ()
    return (
        f"layer {binding.layer_slug!r} binds source {binding.source_slug!r}, which does not implement "
        f"{protocol.__name__} -- that source implements a different layer's contract, so the binding "
        f"would fail inside a lane rather than here",
    )


def assert_region_bindings_are_servable(
    region: Region,
    source_claims: Mapping[str, SourceCoverageClaim],
    contracts: LayerSourceContracts | None = None,
) -> None:
    """Refuse a manifest that binds a layer to a source that cannot serve it, or cannot serve here.

    Three failures, all configuration errors rather than runtime surprises (`federation.md` §2):
    the manifest's declared `coverage` word disagreeing with what the source itself claims, a
    regional source that does not reach every ISO country the region spans, and -- since
    STYLE-REVIEW-W5 B2 -- a source bound to the WRONG LAYER. That last one used to pass every gate
    here (coverage agrees, the ISO codes cover, the slug has a claim) and surfaced as an
    `AttributeError` for a missing lane method on a scheduled turn in the next region; the
    `isinstance` against the layer's `runtime_checkable` protocol is what moves it to boot.

    Bindings whose source has declared no claim yet are left to `unverified_binding_slugs` above,
    and a layer whose protocol has not landed yet is simply absent from `contracts` --
    `federation.md` §5 lands the protocols three layers at a time, so an unchecked layer is the
    migration proceeding, not a defect.
    """
    failures: list[str] = []
    for binding in region.enabled_layers:
        failures.extend(_protocol_conformance_failures(binding, contracts))
        claim = source_claims.get(binding.source_slug)
        if claim is None:
            continue
        if claim.coverage != binding.coverage:
            failures.append(
                f"layer {binding.layer_slug!r} binds source {binding.source_slug!r} as "
                f"{binding.coverage!r}, but that source declares {claim.coverage!r}"
            )
            continue
        uncovered = claim.uncovered_country_codes(region)
        if uncovered:
            failures.append(
                f"layer {binding.layer_slug!r} binds source {binding.source_slug!r}, whose coverage "
                f"{sorted(claim.iso_country_codes)} does not reach {list(uncovered)} in region "
                f"{region.slug!r}"
            )
    if failures:
        raise RegionBindingNotServableError(
            f"region {region.slug!r} has unservable layer bindings:\n" + "\n".join(failures)
        )


__all__ = [
    "LayerSourceContracts",
    "RegionBindingNotServableError",
    "assert_region_bindings_are_servable",
    "unverified_binding_slugs",
]
