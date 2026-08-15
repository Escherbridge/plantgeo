"""Artifact-pinned serving of the literature-grounded recommendation models.

Every response names the artifact digest it was computed from, the label release and review
tier behind it, and the claim tier. No output is a causal effect claim and none is a
publication: the receipts this route reads are `evaluation_only` by CHECK constraint.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict
from sanic import Blueprint, Request
from sanic import json as json_response
from sanic.response import HTTPResponse  # noqa: TC002 - sanic-ext evaluates handler annotations at runtime.
from sqlalchemy import text

from agri_data_service.db.engine import async_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.recommendation_lane import (
    load_covariate_vectors,
    load_site_climate_terms,
)
from agri_data_service.jobs.lease import apply_statement_timeout
from agri_data_service.method.ml.covariates_v2 import CovariateReadError
from agri_data_service.method.ml.recommendation_models import (
    CLAIM_TIER,
    EVALUATION_DISCLAIMER,
    RecommendationTrainingError,
    artifact_from_document,
    rank_subjects,
)

recommendations_bp = Blueprint("recommendations", url_prefix="/recommendations")

_MODEL_KIND_BY_PATH: Final[dict[str, str]] = {
    "species": "species_fit",
    "strategies": "strategy_selection",
}
_LABEL_KIND_BY_MODEL_KIND: Final[dict[str, str]] = {
    "species_fit": "species_fit",
    "strategy_selection": "strategy_outcome",
}
_SHA256_HEX_LENGTH: Final = 64
_MAX_CELL_ID_LENGTH: Final = 64
_MAX_RESULTS: Final = 50
_DEFAULT_RESULTS: Final = 10
_MAX_CITATION_ROWS: Final = 2_000
_MAX_OBJECTIVE_WEIGHT: Final = 1.0

_PINNED_ARTIFACT = text(load_query_sql("routes/recommendation_pinned_artifact.sql"))
_SUBJECT_CITATIONS = text(load_query_sql("routes/recommendation_subject_citations.sql"))
_LOOKUP_CELL = text("SELECT id FROM agri.spatial_cell WHERE id = CAST(:cell_id AS uuid)")


class RecommendationQuery(BaseModel):
    """The validated request: one cell, one knowledge horizon, one optional artifact pin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cell_id: str
    as_of_time: datetime
    observed_date: date
    artifact_checksum: str | None
    wildfire_weight: float
    water_weight: float
    limit: int


class RecommendationPin(BaseModel):
    """Everything a caller needs to reproduce this response byte-for-byte."""

    model_config = ConfigDict(extra="forbid")

    model_name: str
    model_kind: str
    training_key: str
    artifact_checksum: str
    evaluation_checksum: str
    parameter_checksum: str
    training_code_checksum: str
    feature_schema_version: str
    label_release_key: str
    label_release_checksum: str
    harvest_document_checksum: str
    label_review_tier: str
    trained_at: datetime
    evaluation_only: bool
    publication_authorized: bool


@recommendations_bp.get("/species")
async def get_species_recommendations(request: Request) -> HTTPResponse:
    """Rank literature-labelled species for a governed cell, with citations and claim tier."""
    return await _serve(request, model_kind=_MODEL_KIND_BY_PATH["species"])


@recommendations_bp.get("/strategies")
async def get_strategy_recommendations(request: Request) -> HTTPResponse:
    """Rank literature-labelled land-management strategies for a governed cell."""
    return await _serve(request, model_kind=_MODEL_KIND_BY_PATH["strategies"])


async def _serve(  # noqa: PLR0911 - each return is one refusal reason the caller must be able to tell apart
    request: Request, *, model_kind: str
) -> HTTPResponse:
    try:
        query = _parse_query(request)
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=400, headers={"Cache-Control": "no-store"})

    async with async_session() as session:
        await apply_statement_timeout(session)
        pinned = (
            (
                await session.execute(
                    _PINNED_ARTIFACT,
                    {"model_kind": model_kind, "artifact_checksum": query.artifact_checksum},
                )
            )
            .mappings()
            .first()
        )
        if pinned is None:
            return json_response(
                _insufficient(
                    model_kind=model_kind,
                    reason=(
                        "no trained recommendation artifact matches this request"
                        if query.artifact_checksum is None
                        else "no trained recommendation artifact carries the requested checksum"
                    ),
                ),
                status=404,
                headers={"Cache-Control": "no-store"},
            )

        cell = (await session.execute(_LOOKUP_CELL, {"cell_id": query.cell_id})).first()
        if cell is None:
            return json_response(
                {"error": "cell_id does not name a governed agri.spatial_cell"},
                status=404,
                headers={"Cache-Control": "no-store"},
            )

        citations = (
            (
                await session.execute(
                    _SUBJECT_CITATIONS,
                    {
                        "release_key": pinned["label_release_key"],
                        "label_kind": _LABEL_KIND_BY_MODEL_KIND[model_kind],
                        "row_limit": _MAX_CITATION_ROWS,
                    },
                )
            )
            .mappings()
            .all()
        )
        if not citations:
            return json_response(
                _insufficient(
                    model_kind=model_kind,
                    reason=(
                        "the pinned label release holds no agent-reviewed labels of this kind, so no "
                        "recommendation can carry a citation"
                    ),
                    pin=_pin_payload(pinned),
                ),
                status=409,
                headers={"Cache-Control": "no-store"},
            )

        try:
            artifact = artifact_from_document(json.loads(str(pinned["model_document"])))
            site_climate = await load_site_climate_terms(
                session,
                cell_id=query.cell_id,
                issue_days=[query.observed_date],
                as_of_time=query.as_of_time,
            )
            vectors, coverage = await load_covariate_vectors(
                session,
                cell_id=query.cell_id,
                window_start=query.observed_date,
                window_end=query.observed_date,
                as_of_time=query.as_of_time,
                schema_version=str(pinned["feature_schema_version"]),
                wanted_days=[query.observed_date],
            )
        except (CovariateReadError, RecommendationTrainingError, ValueError) as exc:
            return json_response({"error": str(exc)}, status=422, headers={"Cache-Control": "no-store"})

    climate_terms = site_climate.get(query.observed_date)
    if climate_terms is None or not vectors:
        return json_response(
            _insufficient(
                model_kind=model_kind,
                reason=(
                    "the governed streams carry no feature row for this cell at this as-of instant; "
                    "nothing was defaulted to produce one"
                ),
                pin=_pin_payload(pinned),
                extra={"covariate_coverage": coverage.to_summary()},
            ),
            status=409,
            headers={"Cache-Control": "no-store"},
        )

    vector = vectors[0]
    ranked, skipped = rank_subjects(
        artifact,
        site_climate=climate_terms.to_payload(),
        site_covariates=dict(zip(vector.feature_names, vector.feature_values, strict=True)),
        wildfire_weight=query.wildfire_weight,
        water_weight=query.water_weight,
    )
    if not ranked:
        return json_response(
            _insufficient(
                model_kind=model_kind,
                reason=(
                    "every candidate needed a governed value this cell and as-of instant do not "
                    "supply; no candidate was scored from a default"
                ),
                pin=_pin_payload(pinned),
                extra={"unscored_subjects": list(skipped), "covariate_coverage": coverage.to_summary()},
            ),
            status=409,
            headers={"Cache-Control": "no-store"},
        )

    return json_response(
        {
            "claim_tier": CLAIM_TIER,
            "label_review_tier": str(pinned["label_review_tier"]),
            "evaluation_only": bool(pinned["evaluation_only"]),
            "publication_authorized": bool(pinned["publication_authorized"]),
            "disclaimer": EVALUATION_DISCLAIMER,
            "pin": _pin_payload(pinned),
            "request": {
                "cell_id": query.cell_id,
                "as_of_time": query.as_of_time.isoformat(),
                "observed_date": query.observed_date.isoformat(),
                "wildfire_weight": query.wildfire_weight,
                "water_weight": query.water_weight,
                "limit": query.limit,
            },
            "site": {
                "climate": climate_terms.to_payload(),
                "covariate_coverage": coverage.to_summary(),
                "feature_vector_checksum": vector.checksum,
                "max_data_available_at": (
                    None if vector.max_data_available_at is None else vector.max_data_available_at.isoformat()
                ),
            },
            "results": [item.to_payload() for item in ranked[: query.limit]],
            "unscored_subjects": list(skipped),
            "result_count": len(ranked[: query.limit]),
            "candidate_count": len(ranked),
            "cross_validation": pinned["evaluation_metrics"].get("cross_validation")
            if isinstance(pinned["evaluation_metrics"], dict)
            else None,
        },
        headers={"Cache-Control": "no-store"},
    )


def _pin_payload(pinned: Any) -> dict[str, object]:
    """Render the reproducibility pin from the receipt row."""
    return RecommendationPin(
        model_name=str(pinned["model_name"]),
        model_kind=str(pinned["model_kind"]),
        training_key=str(pinned["training_key"]),
        artifact_checksum=str(pinned["artifact_checksum"]),
        evaluation_checksum=str(pinned["evaluation_checksum"]),
        parameter_checksum=str(pinned["parameter_checksum"]),
        training_code_checksum=str(pinned["training_code_checksum"]),
        feature_schema_version=str(pinned["feature_schema_version"]),
        label_release_key=str(pinned["label_release_key"]),
        label_release_checksum=str(pinned["label_release_checksum"]),
        harvest_document_checksum=str(pinned["harvest_document_checksum"]),
        label_review_tier=str(pinned["label_review_tier"]),
        trained_at=pinned["completed_at"],
        evaluation_only=bool(pinned["evaluation_only"]),
        publication_authorized=bool(pinned["publication_authorized"]),
    ).model_dump(mode="json")


def _insufficient(
    *,
    model_kind: str,
    reason: str,
    pin: dict[str, object] | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    """The one honest shape for 'this surface has no citable answer here'."""
    payload: dict[str, object] = {
        "status": "insufficient_labels",
        "model_kind": model_kind,
        "reason": reason,
        "claim_tier": CLAIM_TIER,
        "results": [],
        "pin": pin,
    }
    if extra is not None:
        payload.update(extra)
    return payload


def _parse_query(request: Request) -> RecommendationQuery:
    cell_id = (request.args.get("cell_id") or "").strip()
    if not cell_id or len(cell_id) > _MAX_CELL_ID_LENGTH:
        raise ValueError("cell_id must contain 1 to 64 characters")

    as_of_raw = request.args.get("as_of")
    if not as_of_raw:
        raise ValueError("as_of is required; this surface never substitutes the wall clock for it")
    try:
        as_of_time = datetime.fromisoformat(as_of_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("as_of must be an ISO-8601 timestamp") from exc
    if as_of_time.tzinfo is None or as_of_time.utcoffset() is None:
        raise ValueError("as_of must include a timezone")

    observed_raw = request.args.get("observed_date")
    if observed_raw is None:
        observed_date = as_of_time.date()
    else:
        try:
            observed_date = date.fromisoformat(observed_raw)
        except ValueError as exc:
            raise ValueError("observed_date must be an ISO-8601 calendar date") from exc
        if observed_date > as_of_time.date():
            raise ValueError("observed_date must not be later than as_of; a site is never read from the future")

    artifact_checksum = request.args.get("artifact_checksum")
    if artifact_checksum is not None:
        artifact_checksum = artifact_checksum.strip().lower()
        if len(artifact_checksum) != _SHA256_HEX_LENGTH or not all(
            character in "0123456789abcdef" for character in artifact_checksum
        ):
            raise ValueError("artifact_checksum must be 64 lowercase hexadecimal characters")

    return RecommendationQuery(
        cell_id=cell_id,
        as_of_time=as_of_time.astimezone(UTC),
        observed_date=observed_date,
        artifact_checksum=artifact_checksum,
        wildfire_weight=_bounded_float(request.args.get("wildfire_weight"), "wildfire_weight"),
        water_weight=_bounded_float(request.args.get("water_weight"), "water_weight"),
        limit=_bounded_int(request.args.get("limit"), _DEFAULT_RESULTS, 1, _MAX_RESULTS, "limit"),
    )


def _bounded_float(value: str | None, field_name: str) -> float:
    if value is None:
        return 0.0
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if parsed < 0.0 or parsed > _MAX_OBJECTIVE_WEIGHT:
        raise ValueError(f"{field_name} must be between 0 and {_MAX_OBJECTIVE_WEIGHT}")
    return parsed


def _bounded_int(value: str | None, default: int, minimum: int, maximum: int, field_name: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return parsed


ResponseStatus = Literal["insufficient_labels"]
