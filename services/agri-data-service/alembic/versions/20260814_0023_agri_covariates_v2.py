"""Add the `agri_covariates_v2` feature schema version.

Revision ID: 20260814_0023
Revises: 20260814_0022

Three body-only forward loads, no table touched:

- `covariate_feature_schema` accepts `agri_covariates_v2` and returns the v1 vector
  (indices 1..40, byte-identical) followed by seven forecast-derived and seasonality
  features. A v1 caller's rows are unchanged **by construction**: the new block is a CTE
  whose `WHERE p_schema_version = 'agri_covariates_v2'` makes it empty for v1, and no v1
  row's index, name, kind, stream, lag or window is edited.
- `covariate_daily_features` grows three v2-only branches, each gated on a
  `feature_kind` that v1's schema never emits (`mc_forecast`) or on the
  `per_issue_gating` flag. The v1 branches keep their exact text and their exact
  predicates, so v1 output is unchanged for the same reason.
- `covariate_declared_gap` accepts v2 and declares the two forecast products this
  revision does **not** build -- `analog_ensemble` and `ml_ridge_forecast` -- as explicit
  gaps rather than emitting empty columns that would read as queried-and-absent.

What v2 adds and why each is time-honest:

- indices 41..45, `mc_forecast_*`: what the newest *finalized* Monte-Carlo iteration
  issued strictly before day D said about day D, admitted only if its server-set
  `recorded_at` is at or before both the caller's as-of instant and day D itself.
- indices 46..47, semiannual day-of-year harmonics: deterministic functions of the row's
  own date, like the v1 annual pair.
- per-issue-date as-of gating, replacing the one global knowledge cutoff. A day-D feature
  row now re-takes the latest-available-release pick under day D's own horizon, so a
  revision published after day D is invisible to day D. This is the documented
  revision-leakage gap (`covariate_wind_persist.AS_OF_MODE = 'global'`) closed for v2.
  Drought is gated conservatively -- a polygon that became available after the issue date
  is dropped, turning the day partial rather than admitting a late revision.

The v2 vector is still evaluation-only: none of these functions joins
`v_forecast_series_serving`, `forecast_publication`, `forecast_publication_item` or any
receipt surface, and the iteration plane it reads is itself evaluation-only evidence.
"""

from collections.abc import Sequence

from agri_data_service.db.sql_objects import load_object_sql
from alembic import op

revision = "20260814_0023"
down_revision = "20260814_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Body-only changes: the RETURNS TABLE signatures are untouched, so OR REPLACE is legal
    # and preserves each routine's determinism pins (they are re-stated in the file text).
    op.execute(load_object_sql("functions/covariate_feature_schema.sql", or_replace=True))
    op.execute(load_object_sql("functions/covariate_declared_gap.sql", or_replace=True))
    op.execute(load_object_sql("functions/covariate_daily_features.sql", or_replace=True))


def downgrade() -> None:
    raise NotImplementedError(
        "A feature schema version is an identity, not a setting: artifacts and receipts "
        "already cite agri_covariates_v2 by name. Restore a verified backup into a fresh "
        "database rather than un-defining a version something was trained on."
    )
