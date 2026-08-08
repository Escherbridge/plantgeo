"""Assemble the readiness verdict: config-only flags first, then the database probes in fixed order.

The probe order is load-bearing and must not change: the readiness statement runs first because
the migration revision query runs only when its row says the catalog is readable, so a missing
``alembic_version`` is reported as its own failed check rather than as a revision mismatch. Both
functions leave the session factory and the exception handling to the route in ``__init__``.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agri_data_service.config import settings
from agri_data_service.routes.health.contracts import EXPECTED_ALEMBIC_REVISION
from agri_data_service.routes.health.queries import MIGRATION_STATEMENT, READINESS_STATEMENT


def receiver_identity_ready(profile: str) -> bool:
    """True unless this is the receiver profile without a fully configured publish identity."""
    return profile != "receiver_writer" or bool(
        (
            settings.local_publication_receiver_enabled
            and settings.local_publish_token is not None
            and settings.local_publish_actor is not None
        )
        or (
            settings.historical_promotion_receiver_enabled
            and settings.historical_promotion_token is not None
            and settings.historical_promotion_actor is not None
        )
    )


def initial_checks(profile: str) -> dict[str, bool]:
    """Seed the reported checks: configuration answered now, every database check pessimistic."""
    return {
        "database_profile": profile in {"receiver_writer", "published_reader"},
        "receiver_identity": receiver_identity_ready(profile),
        "extensions": False,
        "migration": False,
        "serving_surface": False,
    }


async def apply_database_probes(session: AsyncSession, checks: dict[str, bool]) -> None:
    """Run the readiness probes in order and fill their verdicts into ``checks`` in place."""
    result = await session.execute(READINESS_STATEMENT)
    row: dict[str, Any] = dict(result.mappings().one())
    checks["extensions"] = bool(row["extensions_ready"])
    checks["serving_surface"] = bool(row["serving_surface_ready"])
    if bool(row["migration_catalog_ready"]):
        migration_result = await session.execute(
            MIGRATION_STATEMENT,
            {"expected_revision": EXPECTED_ALEMBIC_REVISION},
        )
        checks["migration"] = bool(migration_result.scalar_one())
