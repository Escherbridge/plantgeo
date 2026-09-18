"""The boot-time check that every layer binding in a region manifest is actually servable.

See `AGENTS.md` in this directory, section "Source coverage claims", for the rationale and for why
a binding whose source has not declared a claim yet is reported rather than silently accepted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim


class RegionBindingNotServableError(RuntimeError):
    """Raised at startup when a manifest binds a layer to a source that cannot serve the region."""


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


def assert_region_bindings_are_servable(
    region: Region,
    source_claims: Mapping[str, SourceCoverageClaim],
) -> None:
    """Refuse a manifest that binds a layer to a source whose coverage does not contain the region.

    Two failures, both configuration errors rather than runtime surprises (`federation.md` §2): the
    manifest's declared `coverage` word disagreeing with what the source itself claims, and a
    regional source that does not reach every ISO country the region spans. Bindings whose source
    has declared no claim yet are left to `unverified_binding_slugs` above.
    """
    failures: list[str] = []
    for binding in region.enabled_layers:
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
    "RegionBindingNotServableError",
    "assert_region_bindings_are_servable",
    "unverified_binding_slugs",
]
