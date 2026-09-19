"""The `/api/v1/ml` envelope: every answer names its artifact, its issue day and its claim tier.

Layer L4. Why the claim block rides on refusals as well as on answers lives in `AGENTS.md` in this
directory.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final, Literal

from plantgeo_ml_service.method.ml.recommendation_models import EVALUATION_DISCLAIMER, EVALUATION_LABEL
from plantgeo_ml_service.warehouse.lanes import (
    SERVING_PATH_FORECAST_KIND,
    SERVING_PATH_RELEASE_SERIES,
    ServingPath,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The one tier anything this service serves may claim. `EVALUATION_LABEL` is `"evaluation_only"`;
#: named through the constant so the models and the API cannot drift apart.
CLAIM_TIER: Final = EVALUATION_LABEL

#: Route segments and query parameter names, spelled ONCE.
BASE_PATH: Final = "/api/v1/ml"

ROUTE_FIRE_RISK: Final = "fire-risk"
ROUTE_ANALOGS: Final = "analogs"
ROUTE_FORECAST_SUMMARY: Final = "forecast-summary"
ROUTE_ARTIFACTS: Final = "artifacts"

PARAM_LONGITUDE: Final = "lon"
PARAM_LATITUDE: Final = "lat"
PARAM_DAY: Final = "day"
PARAM_LAYER: Final = "layer"
PARAM_CELL_ID: Final = "cell_id"
PARAM_ORIGIN: Final = "origin"

# `SERVING_PATH_FORECAST_KIND`, `SERVING_PATH_RELEASE_SERIES` and `ServingPath` are imported above
# and re-exported here: the wire STATES where a lane's future days live so the web picks its
# day-axis without inferring one from a slug, but `warehouse/lanes.py` DECIDES it. One rule, one
# place -- a second spelling here is how a release-series lane came to be read off the reserved
# `kind=forecast` root (`layer-lanes.md` section 2 carve-out, amended 2026-09-19).

#: WHAT A BODY IS, stated positively, because a content absence is a 200 and a status code alone
#: cannot tell one from an answer. Additive: nothing that already rides on a body moved or changed.
#:
#: - `content`: the payload carries rows or a value, and `error` is null.
#: - `absent`: the warehouse holds nothing for this question. A 200 carrying `error`.
#: - `refused`: serving or the request was at fault, at a 4xx/5xx status.
type Outcome = Literal["content", "absent", "refused"]

OUTCOME_CONTENT: Final[Outcome] = "content"
OUTCOME_ABSENT: Final[Outcome] = "absent"
OUTCOME_REFUSED: Final[Outcome] = "refused"

#: Why a response names no artifact digest. A REASON rather than a bare null, because "we have no
#: artifact" and "this answer was produced without one" are different claims about the same field.
ARTIFACT_ABSENT_NO_ARTIFACT: Final = "no_artifact_published"
ARTIFACT_ABSENT_NOT_MODEL_BACKED: Final = "lane_is_not_model_backed"
ARTIFACT_ABSENT_READ_REFUSED: Final = "read_refused_before_any_artifact_was_resolved"

#: The rows of ONE cell named two different artifacts. Reported as its own reason and never as
#: `lane_is_not_model_backed`: the lane plainly IS model-backed, and "we cannot tell you which
#: model" is a provenance fault an operator must see rather than a property of the lane.
ARTIFACT_ABSENT_PROVENANCE_CONFLICTED: Final = "artifact_provenance_conflicted"


def render_day(value: date) -> str:
    """Render a calendar day as `YYYY-MM-DD`; never converts a zone, because a day has none."""
    return value.isoformat()


def render_instant(value: datetime) -> str:
    """Render an instant in UTC with a `Z` designator, preserving the instant exactly."""
    if value.tzinfo is None:
        raise ValueError("a timezone-naive instant cannot be rendered on the wire without inventing a zone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def render_scalar(value: object) -> object:
    """Render one warehouse cell as JSON: days stay day-shaped, instants carry UTC, bytes go hex."""
    # `bool` is an `int`, so the two arrive here together and both are already JSON.
    if value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        # A non-finite float is not JSON. `null` states "this cell holds no number"; a zero would
        # fabricate a reading, which is the one thing an evaluation-tier answer may never do.
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return render_instant(value)
    if isinstance(value, date):
        return render_day(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    raise ValueError(
        f"a {type(value).__name__} cell has no agreed rendering on this plane; stringifying it would put a value "
        "on the wire under a type the contract never announced"
    )


def render_row(row: Mapping[str, object]) -> dict[str, object]:
    """Render one warehouse row, cell by cell, preserving column names and order."""
    return {name: render_scalar(value) for name, value in row.items()}


@dataclass(frozen=True, slots=True)
class ClaimProvenance:
    """What every `/api/v1/ml` body carries, whether it answers or refuses."""

    #: The artifact digest the answer came from, or `None` with a reason beside it.
    artifact_sha256: str | None
    artifact_absent_reason: str | None
    #: The day the answer was ISSUED FROM, which is not the day it is valid for.
    issued_on: date | None

    def __post_init__(self) -> None:
        if (self.artifact_sha256 is None) == (self.artifact_absent_reason is None):
            raise ValueError(
                "a claim block names exactly one of an artifact digest or the reason it has none; a null digest "
                "with no reason reads as an oversight and a digest with a reason reads as two answers"
            )

    def to_wire(self) -> dict[str, object]:
        """Render the four fields every response carries."""
        return {
            "artifact_sha256": self.artifact_sha256,
            "artifact_absent_reason": self.artifact_absent_reason,
            "issued_on": None if self.issued_on is None else render_day(self.issued_on),
            "claim_tier": CLAIM_TIER,
            "disclaimer": EVALUATION_DISCLAIMER,
        }


def unresolved_claim(reason: str = ARTIFACT_ABSENT_READ_REFUSED) -> ClaimProvenance:
    """Return the claim block a refusal carries when it never got as far as resolving an artifact."""
    return ClaimProvenance(artifact_sha256=None, artifact_absent_reason=reason, issued_on=None)


def answer(payload: Mapping[str, object], *, claim: ClaimProvenance) -> dict[str, object]:
    """Return one answered body: the payload, the claim block, `outcome: content`, `error` null."""
    return {**dict(payload), **claim.to_wire(), "outcome": OUTCOME_CONTENT, "error": None}


def refusal(error: Mapping[str, object], *, outcome: Outcome, claim: ClaimProvenance) -> dict[str, object]:
    """Return one refused body: the claim block is never dropped and `outcome` names which refusal it is."""
    if outcome == OUTCOME_CONTENT:
        raise ValueError("a refusal body may not claim the `content` outcome; that is what `answer` renders")
    return {**claim.to_wire(), "outcome": outcome, **dict(error)}


__all__ = [
    "ARTIFACT_ABSENT_NOT_MODEL_BACKED",
    "ARTIFACT_ABSENT_NO_ARTIFACT",
    "ARTIFACT_ABSENT_PROVENANCE_CONFLICTED",
    "ARTIFACT_ABSENT_READ_REFUSED",
    "BASE_PATH",
    "CLAIM_TIER",
    "EVALUATION_DISCLAIMER",
    "OUTCOME_ABSENT",
    "OUTCOME_CONTENT",
    "OUTCOME_REFUSED",
    "PARAM_CELL_ID",
    "PARAM_DAY",
    "PARAM_LATITUDE",
    "PARAM_LAYER",
    "PARAM_LONGITUDE",
    "PARAM_ORIGIN",
    "ROUTE_ANALOGS",
    "ROUTE_ARTIFACTS",
    "ROUTE_FIRE_RISK",
    "ROUTE_FORECAST_SUMMARY",
    "SERVING_PATH_FORECAST_KIND",
    "SERVING_PATH_RELEASE_SERIES",
    "ClaimProvenance",
    "Outcome",
    "ServingPath",
    "answer",
    "refusal",
    "render_day",
    "render_instant",
    "render_row",
    "render_scalar",
    "unresolved_claim",
]
