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

# The canonical day census, held as unformatted text because its one `{layer_scope}` slot is filled at
# import from the two constants below and never from input -- the same load-time slot `ingest/usdm.py`
# uses for its replace predicate. Loaded exactly once, per sql/AGENTS.md's LOADED rule, and formatted
# twice; ingest/reconcile.py imports the one-layer form from here rather than reading the file again.
_OBSERVED_DAYS_TEMPLATE: Final = load_query_sql("ingest/observed_days.sql")

# The report wants every layer, so it adds no scope clause at all.
_ALL_LAYERS_SCOPE: Final = ""

# Reconciliation wants one layer. The cast pins the bound parameter's type against a uuid column;
# see the scope-slot note in the .sql file for why this is a load-time slot and not a bind.
_ONE_LAYER_SCOPE: Final = "AND features.layer_id = CAST(:layer_id AS uuid)"

_FEATURE_OBSERVED_DAYS: Final[TextClause] = text(_OBSERVED_DAYS_TEMPLATE.format(layer_scope=_ALL_LAYERS_SCOPE))

OBSERVED_DAYS_FOR_LAYER: Final[TextClause] = text(_OBSERVED_DAYS_TEMPLATE.format(layer_scope=_ONE_LAYER_SCOPE))

_FEATURE_VALIDITY_COUNTS: Final[TextClause] = text(load_query_sql("ingest/feature_validity_counts.sql"))

_FEATURE_DUPLICATE_IDENTITIES: Final[TextClause] = text(load_query_sql("ingest/feature_duplicate_identities.sql"))

_DROUGHT_AREA_OBSERVED_DAYS: Final[TextClause] = text(load_query_sql("ingest/drought_area_observed_days.sql"))

_DROUGHT_AREA_VALIDITY_COUNTS: Final[TextClause] = text(load_query_sql("ingest/drought_area_validity_counts.sql"))

_HISTORICAL_OBSERVED_DAYS: Final[TextClause] = text(load_query_sql("ingest/historical_observed_days.sql"))

_HISTORICAL_VALIDITY_COUNTS: Final[TextClause] = text(load_query_sql("ingest/historical_validity_counts.sql"))

_JOB_LANE_STATE: Final[TextClause] = text(load_query_sql("ingest/job_lane_state.sql"))
