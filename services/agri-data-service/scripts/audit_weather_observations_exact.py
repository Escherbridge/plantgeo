"""Emit credential-free exact parity evidence for governed current weather observations.

Read-only. The registry floor defines the first compared day; any older objects are reported as an
excluded Historical Forecast prefix and are never compared to `geo.features`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import text

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.ingest.writer import resolve_layer_id
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.validation.weather_observations_exact import (
    audit_exact_weather_observations,
    settled_cutoff,
)
from agri_data_service.warehouse.schemas.weather_observations import WEATHER_OBSERVATIONS_STREAM


async def audit(last_day: date) -> dict[str, object]:
    """Bracket the independent object-plane walk with two read-only PostgreSQL snapshots."""
    store = ObjectStore.from_settings()
    database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(database_url) as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        layer_id = await resolve_layer_id(session, WEATHER_OBSERVATIONS_STREAM)
        report = await audit_exact_weather_observations(
            session,
            store,
            layer_id=layer_id,
            governed_last_day=last_day,
        )
        await session.rollback()
    return report.to_summary()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only exact PostgreSQL/Parquet audit for the governed current-weather window."
    )
    parser.add_argument(
        "--last-day",
        type=date.fromisoformat,
        default=settled_cutoff(datetime.now(UTC).date()),
        help="inclusive governed cutoff (default: UTC today minus the registry publication lag)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional local path for the credential-free full JSON evidence",
    )
    args = parser.parse_args()
    summary = asyncio.run(audit(args.last_day))
    payload = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{payload}\n", encoding="utf-8")
    print(payload)
    if not summary["clean"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
