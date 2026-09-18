"""Every source implementation that has declared a coverage claim, keyed by its manifest slug.

Lives at `pipeline/` root rather than inside `pipeline/direct/`: a module in `pipeline/direct/` is
itself a lane (`tests/test_layer_import_contract.py::test_lanes_do_not_import_each_other`), and a
registry by definition imports all of them, so it can only live one level up. See
`pipeline/AGENTS.md` section "Source bindings".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim


def declared_source_coverage_claims() -> dict[str, SourceCoverageClaim]:
    """Return `{source_slug: coverage claim}` for every source that implements its layer's protocol.

    The three source modules are imported INSIDE this function on purpose. Each pulls its layer's
    ingest transport (`httpx`) and lane registry, and the only caller is the boot check in
    `app.py`; importing them at module scope would make merely naming this registry pay for the
    whole of `pipeline/direct/` and would put a third import edge into `app.py`'s import graph.

    Sources with no protocol yet are simply absent, which `foundation/region/bindings.py` reports
    through `unverified_binding_slugs` rather than treating as a failure — `federation.md` §5 lands
    the protocols three layers at a time.
    """
    from agri_data_service.pipeline.direct.burn_severity.mtbs import MTBS_BURN_SEVERITY_SOURCE  # noqa: PLC0415
    from agri_data_service.pipeline.direct.drought.usdm import USDM_DROUGHT_SOURCE  # noqa: PLC0415
    from agri_data_service.pipeline.direct.soil_survey.ssurgo import SSURGO_SOIL_SURVEY_SOURCE  # noqa: PLC0415

    return {
        source.source_slug: source.coverage
        for source in (MTBS_BURN_SEVERITY_SOURCE, USDM_DROUGHT_SOURCE, SSURGO_SOIL_SURVEY_SOURCE)
    }


__all__ = ["declared_source_coverage_claims"]
