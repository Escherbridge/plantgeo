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
    """Which `runtime_checkable` Protocol each layer expects, and every source instance UNDER its layer.

    Passed IN from `pipeline/source_bindings.py::declared_layer_source_contracts()` exactly as
    `source_claims` is, because `foundation` may not import `pipeline`
    (`tests/test_layer_import_contract.py`) and the protocol classes live beside their lanes.

    Both maps are keyed by layer, and `sources_by_layer` is deliberately NOT flattened to
    `{source_slug: instance}` (STYLE-REVIEW-W6 B1/S6). The flat map lost the one fact the check
    needs: registration under a layer IS the servability question, so "bound here but registered
    over there" is the exact, signature-independent refusal. An earlier reading -- that a per-layer
    map "would simply not find `ssurgo` under `drought` and would report nothing" -- inverted the
    signal, and the `isinstance` it settled for checks member NAMES only, so `drought -> mtbs`
    passed it and died as a `TypeError` on a scheduled turn.
    """

    #: One layer slug to the `typing.Protocol` it expects, which MUST be `runtime_checkable`.
    protocol_by_layer: Mapping[str, type]
    #: `{layer_slug: {source_slug: instance}}`. A layer absent here is one whose registry has not
    #: landed yet (`federation.md` §5 lands the protocols three layers at a time), not one with no
    #: sources; the difference is why the check is silent for the first and loud for the second.
    sources_by_layer: Mapping[str, Mapping[str, object]]

    def layers_registering(self, source_slug: str) -> tuple[str, ...]:
        """The layer slugs whose own map holds `source_slug`, sorted -- empty when nothing does."""
        return tuple(sorted(layer for layer, sources in self.sources_by_layer.items() if source_slug in sources))


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


def _binding_registration_failures(
    binding: LayerBinding,
    contracts: LayerSourceContracts | None,
) -> tuple[str, ...]:
    """The one-line refusal for a binding whose source is not registered under the bound layer.

    The primary gate is REGISTRATION, not structure: a source is servable for a layer exactly when
    this build registered it under that layer's own map. Two distinct refusals come out of that --
    the source is registered under some OTHER layer (the cross-layer mis-binding, named with the
    layer it actually belongs to) and the source is registered nowhere at all (a slug this build
    has no implementation of, under a layer whose registry HAS landed).

    The `isinstance` against the layer's `runtime_checkable` protocol is kept as a second, weaker
    assertion. It cannot see a cross-layer mis-binding between two protocols that share member
    names -- `typing.runtime_checkable` checks presence, never signatures, so
    `isinstance(MTBS_BURN_SEVERITY_SOURCE, DroughtSource)` is `True` (STYLE-REVIEW-W6 B1) -- but it
    still catches a registered object missing a member its layer's protocol requires.

    Silent for a layer absent from `sources_by_layer`: that is the protocol migration mid-flight,
    and such a binding is reported by `unverified_binding_slugs` instead. Note the asymmetry with
    that function, which is deliberate: an unregistered slug is honest debt while its LAYER has no
    registry, and a hard error once the layer's registry exists to have registered it.
    """
    if contracts is None:
        return ()
    layer_sources = contracts.sources_by_layer.get(binding.layer_slug)
    if layer_sources is None:
        return ()
    source = layer_sources.get(binding.source_slug)
    if source is None:
        registered_under = contracts.layers_registering(binding.source_slug)
        if registered_under:
            return (
                f"layer {binding.layer_slug!r} binds source {binding.source_slug!r}, which this build "
                f"registers under {list(registered_under)}, not under {binding.layer_slug!r} -- that "
                f"source implements a different layer's contract, so the binding would fail inside a "
                f"lane rather than here",
            )
        return (
            f"layer {binding.layer_slug!r} binds source {binding.source_slug!r}, which this build "
            f"registers under no layer at all; {binding.layer_slug!r} registers "
            f"{sorted(layer_sources)}",
        )
    protocol = contracts.protocol_by_layer.get(binding.layer_slug)
    if protocol is not None and not isinstance(source, protocol):
        return (
            f"layer {binding.layer_slug!r} binds source {binding.source_slug!r}, which is registered "
            f"under {binding.layer_slug!r} but does not implement {protocol.__name__} -- the "
            f"registration is stale or the implementation lost a member the layer requires",
        )
    return ()


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
    `AttributeError` for a missing lane method on a scheduled turn in the next region.

    What moves it to boot is the REGISTRY, not a structural check: the source must be registered
    under the bound layer's own map (`_binding_registration_failures` below). The `isinstance`
    against the layer's `runtime_checkable` protocol is a second, weaker assertion on top of that,
    because a runtime-checkable protocol compares member NAMES only and two sibling lanes that both
    publish dated releases satisfy each other's protocol (STYLE-REVIEW-W6 B1).

    Bindings whose source has declared no claim yet are left to `unverified_binding_slugs` above,
    and a layer whose registry has not landed yet is simply absent from `contracts` --
    `federation.md` §5 lands the protocols three layers at a time, so an unchecked layer is the
    migration proceeding, not a defect.
    """
    failures: list[str] = []
    for binding in region.enabled_layers:
        failures.extend(_binding_registration_failures(binding, contracts))
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
