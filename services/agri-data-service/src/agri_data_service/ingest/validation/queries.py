"""The report's SQL, bound once at import.

Every statement is schema-qualified (`db/base.py` sets no search_path, so an unqualified name resolves to
nothing), parameterised, and read-only. Each opens with a `-- <name>` marker the unit tests match on, and each
lives in its own file under `sql/ingest/` where its parameters and a clause-by-clause walkthrough are
documented. The two `SET LOCAL` statements below stay inline: one line each, no bind parameters (a `SET LOCAL`
cannot take one), and one of them bakes in a module constant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.ingest.validation.constants import STATEMENT_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import TextClause

_SET_READ_ONLY_SNAPSHOT: Final[TextClause] = text("-- set_read_only_snapshot\nSET LOCAL transaction_read_only = on")

_SET_STATEMENT_TIMEOUT: Final[TextClause] = text(
    f"-- set_statement_timeout\nSET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_SECONDS}s'"
)

_SERVER_DAY: Final[TextClause] = text(load_query_sql("ingest/server_day.sql"))

_FEATURE_OBSERVED_DAYS: Final[TextClause] = text(load_query_sql("ingest/feature_observed_days.sql"))

_FEATURE_VALIDITY_COUNTS: Final[TextClause] = text(load_query_sql("ingest/feature_validity_counts.sql"))

_FEATURE_DUPLICATE_IDENTITIES: Final[TextClause] = text(load_query_sql("ingest/feature_duplicate_identities.sql"))

_DROUGHT_AREA_OBSERVED_DAYS: Final[TextClause] = text(load_query_sql("ingest/drought_area_observed_days.sql"))

_DROUGHT_AREA_VALIDITY_COUNTS: Final[TextClause] = text(load_query_sql("ingest/drought_area_validity_counts.sql"))

_HISTORICAL_OBSERVED_DAYS: Final[TextClause] = text(load_query_sql("ingest/historical_observed_days.sql"))

_HISTORICAL_VALIDITY_COUNTS: Final[TextClause] = text(load_query_sql("ingest/historical_validity_counts.sql"))

_JOB_LANE_STATE: Final[TextClause] = text(load_query_sql("ingest/job_lane_state.sql"))
