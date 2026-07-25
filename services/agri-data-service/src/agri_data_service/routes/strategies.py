"""Read-only publication boundary for reviewed strategy definitions."""

import uuid
from typing import Any

from sanic import Blueprint, Request, json
from sanic.response import HTTPResponse
from sqlalchemy import func, select

from agri_data_service.db.engine import async_session
from agri_data_service.models.strategy import Strategy, StrategyReviewState

strategies_bp = Blueprint("strategies", url_prefix="/strategies")
_MAX_PAGE_SIZE = 100
_MAX_OFFSET = 10_000
_MAX_CATEGORY_LENGTH = 100


@strategies_bp.get("/")
async def list_strategies(request: Request) -> HTTPResponse:
    """List only evidence-reviewed strategies through bounded pagination."""
    try:
        limit = _bounded_query_int(request.args.get("limit"), 20, 1, _MAX_PAGE_SIZE)
        offset = _bounded_query_int(request.args.get("offset"), 0, 0, _MAX_OFFSET)
    except ValueError as exc:
        return json({"error": str(exc)}, status=400)

    category = request.args.get("category")
    if category is not None:
        category = category.strip()
        if not category or len(category) > _MAX_CATEGORY_LENGTH:
            return json({"error": "category must contain 1 to 100 characters"}, status=400)

    filters = [Strategy.review_state == StrategyReviewState.APPROVED]
    if category:
        filters.append(Strategy.category == category)

    async with async_session() as session:
        total = int((await session.execute(select(func.count()).select_from(Strategy).where(*filters))).scalar_one())
        records = (
            await session.scalars(
                select(Strategy).where(*filters).order_by(Strategy.name, Strategy.id).limit(limit).offset(offset)
            )
        ).all()

    return json(
        {
            "data": [_serialize_strategy(strategy) for strategy in records],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        headers={"Cache-Control": "no-store"},
    )


@strategies_bp.get("/<strategy_id:uuid>")
async def get_strategy(_request: Request, strategy_id: uuid.UUID) -> HTTPResponse:
    """Return an approved strategy or conceal its unpublished state."""
    async with async_session() as session:
        strategy = await session.scalar(
            select(Strategy).where(
                Strategy.id == strategy_id,
                Strategy.review_state == StrategyReviewState.APPROVED,
            )
        )
    if strategy is None:
        return json({"error": "strategy_not_found"}, status=404)
    return json(_serialize_strategy(strategy), headers={"Cache-Control": "no-store"})


def _bounded_query_int(
    value: str | None,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("pagination values must be integers") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"pagination value must be between {minimum} and {maximum}")
    return parsed


def _serialize_strategy(strategy: Strategy) -> dict[str, Any]:
    return {
        "id": str(strategy.id),
        "name": strategy.name,
        "slug": strategy.slug,
        "category": strategy.category,
        "authority": strategy.authority,
        "practiceCode": strategy.practice_code,
        "description": strategy.description,
        "suitability": {
            "precipitationMm": [strategy.min_precip_mm, strategy.max_precip_mm],
            "temperatureC": [strategy.min_temp_c, strategy.max_temp_c],
            "soilTypes": strategy.suitable_soil_types,
            "drainage": strategy.suitable_drainage,
            "maxSlopePct": strategy.max_slope_pct,
            "minOrganicMatterPct": strategy.min_organic_matter_pct,
        },
        "characteristics": {
            "waterRequirement": (strategy.water_requirement.value if strategy.water_requirement else None),
            "laborIntensity": (strategy.labor_intensity.value if strategy.labor_intensity else None),
            "timeToYieldYears": strategy.time_to_yield_years,
            "carbonSequestrationPotential": (
                strategy.carbon_seq_potential.value if strategy.carbon_seq_potential else None
            ),
            "biodiversityImpact": (strategy.biodiversity_impact.value if strategy.biodiversity_impact else None),
        },
        "evidence": {
            "citation": strategy.evidence_citation,
            "sourceUrl": strategy.evidence_source_url,
            "jurisdiction": strategy.jurisdiction,
            "limitations": strategy.limitations,
            "reviewedAt": (strategy.reviewed_at.isoformat() if strategy.reviewed_at else None),
            "reviewedBy": strategy.reviewed_by,
        },
    }
