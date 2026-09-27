"""The SSURGO binding of the soil-survey source protocol: United States, and no LIVE pull implemented.

SSURGO is what the PNW manifest binds `soil-survey` to, and this module is where that claim now
lives. The live per-region pull below does NOT exist: the Postgres-era ingest module was retired in
the 2026-09 cleanup, and `pipeline/parquet/lane_registry.py` still refuses this lane's retired
database watermark in as many words. As of the 2026-09-27 SSURGO port, `pipeline/direct/
soil_survey/__main__.py` runs an offline, explicit capture-and-candidate CLI against the same USDA
source, but that CLI answers no `SoilSurveySource.fetch_release` call and is registered as no lane
-- it produces local, unpublished receipts an operator stages and admits by hand (`AGENTS.md`, "Why
the pull raises"). This binding still has nothing to return until a release is admitted and wired
back into this protocol, which is explicitly out of scope for the acquisition slice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.pipeline.direct.soil_survey.source_protocol import (
        BoundingBox,
        SoilSurveyRelease,
        SoilSurveySource,
    )

#: The region manifest's `source_slug` for this implementation (`foundation/region/pnw.json`).
SSURGO_SOURCE_SLUG: Final = "ssurgo"

#: USDA NRCS publishes SSURGO for the United States and its territories only; a source-system fact,
#: not this deployment's footprint (`foundation/region/AGENTS.md`, "Source coverage claims").
SSURGO_COVERAGE: Final = SourceCoverageClaim(coverage="regional", iso_country_codes=("US",))

#: Why every pull path below refuses, quoted wherever the refusal is raised. The 2026-09-27 port
#: added an offline capture CLI against this same source (`__main__.py`), but it is not this
#: protocol's `fetch_release`, is not registered as a lane, and admits nothing on its own -- so this
#: reason still holds until an operator-admitted release is wired back into the live pull below.
SSURGO_PULL_RETIRED_REASON: Final = (
    "the SSURGO ingest module was retired with the Postgres cleanup and no source-direct SSURGO "
    "lane has been admitted; publish one before asking this binding for a release"
)


class SsurgoPullRetiredError(NotImplementedError):
    """Raised when the SSURGO binding is asked to pull, which no code path does today."""


class SsurgoSoilSurveySource:
    """The pilot's soil-survey binding: USDA NRCS SSURGO, coverage declared, pull not implemented.

    Declaring the binding without the pull is the honest state of this layer, and it is what the
    region binding check needs: the manifest already says `soil-survey` is filled by `ssurgo`, and
    a check that cannot see the source's coverage claim can only ever pass vacuously. Both pull
    methods raise rather than returning an empty release, because an empty release is a *claim*
    that the source published nothing, and this lane has made no such observation.
    """

    source_slug = SSURGO_SOURCE_SLUG
    coverage = SSURGO_COVERAGE

    async def source_vintage_watermark(self) -> date:
        """Refuse: this protocol has no LIVE producer for SSURGO's `sacatalog.saverest` watermark.

        The offline capture CLI (`__main__.py`) records a `saverest` per area it captures, but that
        receipt is not wired back into this per-region method.
        """
        raise SsurgoPullRetiredError(SSURGO_PULL_RETIRED_REASON)

    async def fetch_release(
        self,
        *,
        bounding_box: BoundingBox,  # noqa: ARG002 -- protocol signature; body raises before use
        survey_area_symbols: Sequence[str],  # noqa: ARG002 -- protocol signature; body raises before use
    ) -> SoilSurveyRelease:
        """Refuse: no source-direct SSURGO pull exists; see `SSURGO_PULL_RETIRED_REASON`."""
        raise SsurgoPullRetiredError(SSURGO_PULL_RETIRED_REASON)


#: The single instance the manifest's `ssurgo` binding resolves to; stateless, so one is enough.
SSURGO_SOIL_SURVEY_SOURCE: SoilSurveySource = SsurgoSoilSurveySource()


__all__ = [
    "SSURGO_COVERAGE",
    "SSURGO_PULL_RETIRED_REASON",
    "SSURGO_SOIL_SURVEY_SOURCE",
    "SSURGO_SOURCE_SLUG",
    "SsurgoPullRetiredError",
    "SsurgoSoilSurveySource",
]
