"""Pin routine search_path so the warehouse restores from its own dump, and finish the checksum float pin.

Revision ID: 20260803_0017
Revises: 20260802_0016

Two defects, one revision. Both were found by the populated-restore rehearsal in
``plans/postgresql-18-migration-rehearsal-2026-08-02.md`` (§10.2, §10.2.1);
neither is specific to PostgreSQL 18 and both reproduce on the canonical pg16.

**Defect 1 -- the warehouse cannot be restored from its own ``pg_dump`` output.**
``pg_restore`` runs with ``search_path = ''``. ``forecast_iteration_value.value_checksum``
is a *stored generated* column, so its values are omitted from the dump and
**recomputed by the restoring server during COPY**; that calls
``agri.forecast_iteration_value_checksum``, which calls ``digest()`` unqualified.
``pgcrypto`` lives in ``public``, so the call cannot resolve and the data load
aborts with ``function digest(text, unknown) does not exist``. Exactly one
routine blocks a restore today, but 21 share the latent pattern: they call a
``public``-schema extension function unqualified and pin no ``search_path``, and
resolve at runtime only because ordinary sessions have a non-empty one. All 21
are pinned here -- see ``SEARCH_PATH_DEPENDENT_ROUTINES``.

The 21 were re-derived from the live catalogue rather than copied from the
report, because the report's detection pattern was case-sensitive (a lowercase
``st_*`` call would have been missed) and not dot-aware. The corrected
derivation is comment-stripped, case-insensitive, and rejects
schema-qualified calls; it also excludes names that live in ``pg_catalog``
(``encode``, ``gen_random_uuid``), which resolve regardless of ``search_path``.
It disagrees with the report on membership, not on the total: the report counted
``enforce_intervention_lineage_release_membership`` as one of the 21 and
subtracted it again as "already pinned", but that routine calls
``public.digest(...)`` **schema-qualified** and never belonged in the set. The
number of routines actually needing the pin is therefore **21, not 20**, and
none of them already carried a ``search_path``.

``ALTER ROUTINE ... SET search_path`` is used deliberately instead of
``CREATE OR REPLACE``: it *adds* to ``proconfig`` and preserves each routine's
existing determinism settings, whereas a replace that omitted them would
silently drop the ``TimeZone``/``DateStyle``/``IntervalStyle``/``extra_float_digits``
pins that make the checksums reproducible. No function body changes.

Every ``agri`` object these 21 routines touch is already schema-qualified
(verified against the catalogue), so ``public, pg_catalog`` is sufficient; the
value matches the one the restore rehearsal proved out. ``pg_catalog`` is named
explicitly rather than left implicit so the resolution order is on the record.
None of the 21 is ``SECURITY DEFINER`` -- the twelve that are already pin
``pg_catalog, agri`` and are untouched here.

**Defect 2 -- a live receipt-integrity defect.**
``agri.forecast_hindcast_value_checksum`` renders six ``double precision``
arguments through ``::text`` while pinning only ``TimeZone``. Session
``extra_float_digits`` therefore leaks into a governed checksum: measured on the
pg18 rehearsal container, identical inputs produced
``5e460095427ac991...`` at ``extra_float_digits = 0`` and ``5ef858240aa8a7d5...``
at ``3``. The correct control, ``forecast_iteration_value_checksum``, pins the
full rendering set and returns one digest at both settings. This revision brings
the hindcast function to the same four GUCs. It is harmless only while
``forecast_hindcast_value`` is empty: the finalization and immutability
machinery treats ``value_checksum`` as identity, so two sessions with different
``extra_float_digits`` would otherwise write two different checksums for the
same row.

``strategy_label_bundle_checksum`` was audited in the same pass, as the report
asks. **It carries no live defect**: it hashes a ``jsonb`` export, and ``jsonb``
renders date/time values as ISO-8601 regardless of ``DateStyle`` (measured), the
bundle contains no ``interval``-typed field, and the one axis that *does* reach
it -- ``extra_float_digits``, via the ``double precision`` outcome and evidence
values -- is already pinned. The remaining two GUCs are added anyway so the
governed-checksum family is uniform and a later bundle field cannot
reintroduce the defect silently. ``export_strategy_label_bundle`` is pinned
identically: it is the function that actually performs the rendering, it is
callable directly (the trainer hashes its exact export text), and hardening only
its wrapper is precisely the "appears hardened" outcome §10.2.1 warns about.

Unlike the data-bearing revisions, this one has a real ``downgrade()``: a
``proconfig`` change is exactly reversible and destroys no evidence. Reversing
it does, however, reinstate both defects -- including a schema that no
unattended ``pg_restore`` can load.
"""

from collections.abc import Sequence

from alembic import op

revision = "20260803_0017"
down_revision = "20260802_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The 21 agri routines that call a public-schema extension function (digest,
# crypt, gen_salt, hmac, ST_*) unqualified. Hardcoded rather than re-derived at
# migration time: a revision must apply identically to every database that
# replays it, so it may not branch on the catalogue it finds.
SEARCH_PATH_DEPENDENT_ROUTINES: tuple[str, ...] = (
    "agri.covariate_vector_manifest(uuid, timestamptz, timestamptz, timestamptz, varchar)",
    "agri.drought_class_daily_series(uuid, timestamptz, timestamptz, timestamptz)",
    "agri.finalize_forecast_receipt(uuid, varchar)",
    "agri.finalize_strategy_label_release(uuid, varchar)",
    "agri.finalize_strategy_selection_receipt(uuid, varchar)",
    "agri.forecast_aligned_daily_series(uuid, uuid, timestamptz, timestamptz, timestamptz, varchar)",
    "agri.forecast_daily_bootstrap("
    "uuid, uuid, timestamptz, timestamptz, timestamptz, integer, integer, bigint, varchar,"
    " double precision, double precision)",
    "agri.forecast_iteration_evaluation(uuid, timestamptz)",
    "agri.forecast_iteration_receipt_checksum(uuid)",
    "agri.forecast_iteration_value_checksum("
    "timestamptz, integer, double precision, double precision, double precision, integer, varchar)",
    "agri.forecast_timeseries_base(uuid, timestamptz)",
    "agri.materialize_forecast_iteration("
    "uuid, varchar, uuid, uuid, timestamptz, timestamptz, timestamptz, integer, integer, bigint, varchar,"
    " double precision, double precision)",
    "agri.publish_forecast_publication(uuid, varchar)",
    "agri.reconcile_forecast_iteration_actuals(integer, uuid, uuid, timestamptz)",
    "agri.strategy_label_episode_checksum(uuid)",
    "agri.strategy_label_release_checksum(uuid)",
    "agri.strategy_outcome_definition_checksum(agri.strategy_outcome_definition)",
    "agri.strategy_selection_candidate_checksum(uuid)",
    "agri.strategy_selection_policy_checksum(agri.strategy_selection_policy)",
    "agri.strategy_selection_receipt_checksum(uuid)",
    "agri.verify_forecast_iteration_actual_lineage()",
)

# `ALTER ROUTINE` covers both prokind 'f' and prokind 'p'; two of the 21
# (materialize_forecast_iteration, reconcile_forecast_iteration_actuals) are
# procedures, so a bare `ALTER FUNCTION` would fail on them.
_SEARCH_PATH_PIN = "public, pg_catalog"

# The rendering GUCs `forecast_iteration_value_checksum` pins, quoted so the
# mixed-case GUC names land in proconfig exactly as the control spells them.
_FULL_RENDERING_PINS = (
    ('"DateStyle"', "'ISO, MDY'"),
    ('"IntervalStyle"', "'postgres'"),
    ("extra_float_digits", "'1'"),
)

_HINDCAST_VALUE_CHECKSUM = (
    "agri.forecast_hindcast_value_checksum("
    "timestamptz, integer, double precision, double precision, double precision, double precision,"
    " double precision, double precision, uuid, varchar)"
)

# Already pin TimeZone and extra_float_digits; only the format GUCs are missing.
_LABEL_BUNDLE_ROUTINES: tuple[str, ...] = (
    "agri.strategy_label_bundle_checksum(uuid)",
    "agri.export_strategy_label_bundle(uuid)",
)
_FORMAT_ONLY_PINS = _FULL_RENDERING_PINS[:2]


def upgrade() -> None:
    for routine in SEARCH_PATH_DEPENDENT_ROUTINES:
        op.execute(f"ALTER ROUTINE {routine} SET search_path TO {_SEARCH_PATH_PIN};")

    settings = " ".join(f"SET {name} TO {value}" for name, value in _FULL_RENDERING_PINS)
    op.execute(f"ALTER FUNCTION {_HINDCAST_VALUE_CHECKSUM} {settings};")

    settings = " ".join(f"SET {name} TO {value}" for name, value in _FORMAT_ONLY_PINS)
    for routine in _LABEL_BUNDLE_ROUTINES:
        op.execute(f"ALTER ROUTINE {routine} {settings};")


def downgrade() -> None:
    resets = " ".join(f"RESET {name}" for name, _ in _FORMAT_ONLY_PINS)
    for routine in _LABEL_BUNDLE_ROUTINES:
        op.execute(f"ALTER ROUTINE {routine} {resets};")

    # Back to the single `TimeZone` pin this revision found it with.
    resets = " ".join(f"RESET {name}" for name, _ in _FULL_RENDERING_PINS)
    op.execute(f"ALTER FUNCTION {_HINDCAST_VALUE_CHECKSUM} {resets};")

    # `RESET search_path` removes only that entry from proconfig, leaving each
    # routine's determinism pins in place -- the exact inverse of `upgrade()`.
    for routine in SEARCH_PATH_DEPENDENT_ROUTINES:
        op.execute(f"ALTER ROUTINE {routine} RESET search_path;")
