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

# The canonical day census, held as unformatted text because its `{layer_scope}` and `{day_scope}` slots
# are filled at import from the closed constants below and never from input -- the same load-time slot
# `ingest/usdm.py` uses for its replace predicate. Loaded exactly once, per sql/AGENTS.md's LOADED rule,
# and formatted twice; ingest/reconcile.py imports the one-layer, day-ranged form from here rather than
# reading the file again.
_OBSERVED_DAYS_TEMPLATE: Final = load_query_sql("ingest/observed_days.sql")

# The report wants every layer, so it adds no scope clause at all.
_ALL_LAYERS_SCOPE: Final = ""

# Reconciliation wants one layer. The cast pins the bound parameter's type against a uuid column;
# see the scope-slot note in the .sql file for why this is a load-time slot and not a bind.
_ONE_LAYER_SCOPE: Final = "AND features.layer_id = CAST(:layer_id AS uuid)"

# The day scope slot's two fillings that apply wherever the scanned rows are already known to carry a
# parseable observation day -- observed_days.sql's own WHERE clause filters `IS NOT NULL` before this
# slot ever runs, and feature_validity_counts.sql's day-range form wants exactly the same predicate over
# the same expression. Shared here rather than spelled twice, so the two files cannot drift apart on what
# "a day range" means. See each .sql file's own "THE DAY SCOPE SLOT" note for why this predicate looks
# the way it does and why it is a load-time slot rather than a nullable bind.
_ALL_DAYS_SCOPE: Final = ""
_DAY_RANGE_SCOPE: Final = (
    "AND geo.feature_observation_day(features.properties) >= CAST(:from_day AS date)\n"
    "   AND geo.feature_observation_day(features.properties) <= CAST(:to_day AS date)"
)

_FEATURE_OBSERVED_DAYS: Final[TextClause] = text(
    _OBSERVED_DAYS_TEMPLATE.format(layer_scope=_ALL_LAYERS_SCOPE, day_scope=_ALL_DAYS_SCOPE)
)

# Reconciliation shards its census across several executions of this statement (see
# ingest/reconcile.py's `observed_day_shards` and `_observed_days_in_shard`), so the one-layer form
# always carries an inclusive day-range predicate -- even a caller that wants the layer's whole life
# binds the widest dates the calendar has (`ingest.reconcile.UNBOUNDED_FIRST_OBSERVED_DAY` /
# `UNBOUNDED_LAST_OBSERVED_DAY`) rather than asking this statement to omit the predicate.
OBSERVED_DAYS_FOR_LAYER: Final[TextClause] = text(
    _OBSERVED_DAYS_TEMPLATE.format(layer_scope=_ONE_LAYER_SCOPE, day_scope=_DAY_RANGE_SCOPE)
)

_FEATURE_VALIDITY_COUNTS_TEMPLATE: Final = load_query_sql("ingest/feature_validity_counts.sql")

# The complement of `_DAY_RANGE_SCOPE`, and specific to this one statement: feature_validity_counts.sql
# counts undated rows on purpose (the `undated_day` check) and its other counters want every published
# row regardless of date, so a caller sharding this scan by day owes one extra execution of this form --
# no date range, however wide, ever matches a row whose observation day is NULL. See
# feature_validity_counts.sql's own "THE DAY SCOPE SLOT" note.
_UNDATED_DAY_SCOPE: Final = "AND geo.feature_observation_day(features.properties) IS NULL"

_FEATURE_VALIDITY_COUNTS: Final[TextClause] = text(_FEATURE_VALIDITY_COUNTS_TEMPLATE.format(day_scope=_ALL_DAYS_SCOPE))

# A caller sharding the validity scan by day (reusing `ingest.reconcile.observed_day_shards` for the
# cutting, never a second copy of that arithmetic) binds one shard's `from_day`/`to_day` per execution
# against this form, then sums every counter across those executions AND one execution of
# `FEATURE_VALIDITY_COUNTS_FOR_UNDATED_ROWS` to reproduce `_FEATURE_VALIDITY_COUNTS`'s own grand total.
FEATURE_VALIDITY_COUNTS_FOR_DAY_RANGE: Final[TextClause] = text(
    _FEATURE_VALIDITY_COUNTS_TEMPLATE.format(day_scope=_DAY_RANGE_SCOPE)
)

FEATURE_VALIDITY_COUNTS_FOR_UNDATED_ROWS: Final[TextClause] = text(
    _FEATURE_VALIDITY_COUNTS_TEMPLATE.format(day_scope=_UNDATED_DAY_SCOPE)
)

_FEATURE_DUPLICATE_IDENTITIES: Final[TextClause] = text(load_query_sql("ingest/feature_duplicate_identities.sql"))

# _DROUGHT_AREA_OBSERVED_DAYS and _HISTORICAL_OBSERVED_DAYS build the same class of full-table scan
# observed_days.sql did before its day scope slot landed. They are NOT sharded here: their .sql files
# carry no `{day_scope}` slot to fill, and adding one is a change to files this module does not own in
# this pass (drought_area_observed_days.sql scans geo.drought_areas keyed on a VARCHAR valid_date;
# historical_observed_days.sql UNIONs three geo.historical_* tables). Whoever adds that slot should shape
# it the way `_DAY_RANGE_SCOPE` above is shaped -- an inclusive range over the statement's own day
# expression, cast to date, filled at import time from a closed constant -- and the caller that fans the
# shards out should reuse `ingest.reconcile.observed_day_shards` for the cutting rather than a third copy
# of the same arithmetic.
_DROUGHT_AREA_OBSERVED_DAYS: Final[TextClause] = text(load_query_sql("ingest/drought_area_observed_days.sql"))

_DROUGHT_AREA_VALIDITY_COUNTS: Final[TextClause] = text(load_query_sql("ingest/drought_area_validity_counts.sql"))

_HISTORICAL_OBSERVED_DAYS: Final[TextClause] = text(load_query_sql("ingest/historical_observed_days.sql"))

_HISTORICAL_VALIDITY_COUNTS: Final[TextClause] = text(load_query_sql("ingest/historical_validity_counts.sql"))

_JOB_LANE_STATE: Final[TextClause] = text(load_query_sql("ingest/job_lane_state.sql"))
