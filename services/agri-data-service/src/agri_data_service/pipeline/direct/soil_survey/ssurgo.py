"""The SSURGO binding of the soil-survey source protocol: United States, and no pull implemented.

SSURGO is what the PNW manifest binds `soil-survey` to, and this module is where that claim now
lives. The pull itself does NOT exist: the Postgres-era ingest module was retired in the 2026-09
cleanup and no source-direct SSURGO lane has been admitted since
(`pipeline/parquet/lane_registry.py` refuses the retired database watermark in as many words).
See `AGENTS.md` in this directory, section "Why the pull raises".
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

#: Why every pull path below refuses, quoted wherever the refusal is raised.
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
        """Refuse: SSURGO's `sacatalog.saverest` watermark has no producer in this tree today."""
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
