"""Retire the forecast governance enforcement layer and the hindcast plane, keeping every checksum column.

Revision ID: 20260803_0018
Revises: 20260803_0017

This revision implements **Option 4** of
``plans/checksum-layer-audit-2026-08-03.md`` (§6), extended by the owner with
the hindcast plane and three of the four locked owner roles. The audit's
finding, in one line: the *checksum columns* earn their keep and stay, the
*database-level enforcement built on top of them* does not. §3.1 shows why —
tamper-evidence needs the digest to be beyond the reach of the party who might
tamper, and here the researcher, the DBA and the hypothetical adversary are one
person with one credential; the repo already ships two copy-pasteable trigger
bypasses (``…0014:169``, ``tests/test_forecasting_v1_upgrade_postgresql.py:209``).
What remains is accident-prevention, and §3.2 shows that is already delivered by
the checksum columns plus ``materialize_forecast_iteration``'s idempotency
block, both of which are retained.

The owner chose to fold every repair into one revision rather than sequence
them, so this file drops 48 triggers, 34 routines, 2 tables, 1 view, 10 CHECK
constraints and 3 roles, converts 1 generated column to plain storage, flips 1
DEFAULT, and amends 1 procedure.

**What is kept, and why the keeps are not negotiable.**

*The nine tables* ``agri.v_forecast_series_serving`` joins —
``publication_pointer``, ``forecast_publication``, ``forecast_publication_item``,
``forecast_receipt``, ``forecast_value``, ``forecast_series``, ``forecast_run``,
``forecast_feature_snapshot``, ``forecast_training_run`` — survive as plain
storage. They carry ``agri.mv_forecast_ml_daily_serving``, the ML serving lane
that ``plans/ingestion-warehouse-consolidation-2026-08-03.md`` §6 depends on;
the audit's §7 item 2 correction records the owner's ruling that "the ml must
stay". Only their machinery goes.

*All 61 plain checksum columns and their 29 format CHECKs* stay. They are the
reproducibility record and cost nothing at runtime; §5.2 measures the blast
radius of removing them at 31 Python files and 12 of 33 CLI commands.

*``ck_forecast_receipt_finalized_evidence``* (``db/agri/tables/forecast_receipt.sql:24``)
is the one status-to-checksum evidence CHECK retained. ``forecast_receipt.receipt_checksum``
is the column the ML serving view's ``status = 'finalized'`` filter depends on and
it has no SQL function behind it at all, so the digest must be computed in
Python; the CHECK is the only thing left asserting the two move together.

It did not actually assert that, and this revision repairs it. As written the CHECK read
``status <> 'finalized' OR (receipt_checksum ~ '^[0-9a-f]{64}$' AND finalized_at IS NOT NULL)``.
Under a NULL ``receipt_checksum`` the regex evaluates to NULL, ``NULL AND TRUE`` is NULL, and
``FALSE OR NULL`` is NULL — and a CHECK constraint rejects only FALSE, so a receipt with no
digest at all could be finalized and admitted to the serving lane. That was survivable while
``verify_forecast_receipt_finalization`` and ``finalize_forecast_receipt`` still raised on a
NULL digest; those are dropped here, which would have left the hole as the *only* guard. The
constraint is rebuilt with an explicit ``receipt_checksum IS NOT NULL`` conjunct so it
evaluates FALSE rather than NULL. This is the same class of silent failure as the
``value_checksum`` conversion described below: a governed column that stops covering anything
while every format check still passes.

*``guard_forecast_immutable_rows``* (6 triggers on the append-only reference
tables) and *``guard_strategy_review_change``* (2 triggers) are the only guards
retained. The second is retained because
``db/agri/functions/guard_strategy_review_change.sql:35-36,53-54`` assigns
``NEW.definition_checksum := agri.strategy_outcome_definition_checksum(NEW)``
and ``NEW.policy_checksum := agri.strategy_selection_policy_checksum(NEW)``. It
is the **sole caller** of both checksum functions, which Option 4 keeps;
dropping it would leave both columns permanently NULL.

*All seven ``record_*`` writers and their 16 triggers* are retained, but converted from
``SECURITY DEFINER`` to ``SECURITY INVOKER``. They were ``SECURITY DEFINER`` so a restricted
role could append to ``agri.forecast_input_recorded_at`` without holding INSERT on it, owned
by the locked NOLOGIN ``plantgeo_forecast_input_recorder_owner``. Retiring that role
reassigns them to whoever runs this migration, so leaving them ``SECURITY DEFINER`` would
silently *widen* their privilege surface rather than preserve it. Since the four-role model
is rejected and every caller connects with the same credentials, the definer bit no longer
buys anything and is dropped. They feed
``agri.forecast_input_recorded_at``, which
``db/agri/views/v_forecast_timeseries_contract.sql:45-46`` INNER JOINs and
``db/agri/functions/forecast_timeseries_contract.sql:86,91`` reads, and on which
``db/agri/functions/forecast_daily_bootstrap.sql:67-72`` does
``IF NOT FOUND THEN RAISE EXCEPTION``. Dropping them would hard-RAISE for any
series or data source registered after this revision, breaking
``materialize_forecast_iteration``, ``reconcile_forecast_iteration_actuals`` and
``forecast_aligned_daily_series`` — all of which are kept. The audit lists them
under "bookkeeping" (§2); that classification is wrong about their live
coupling.

*The ``require_*`` family* (3 functions, 10 triggers) is retained. Option 4
scopes the cut to ``finalize_*`` / ``verify_*`` / ``guard_*`` / ``enforce_*``,
extended here by ``protect_*`` / ``reject_*`` / ``prevent_*``; ``require_*``
appears in neither list and is left in place deliberately, not by oversight.

*``publish_forecast_publication``, ``validate_forecast_run``,
``validate_forecast_feature_snapshot`` and ``validate_forecast_training_run``*
are also outside every dropped family and survive, so two of the serving view's
three state predicates (``run.status = 'validated'``,
``publication.state = 'published'``) still have an in-database mover.

**The generated column, and the silent receipt failure it hides.**

``forecast_iteration_value.value_checksum`` was
``GENERATED ALWAYS AS (agri.forecast_iteration_value_checksum(...)) STORED``.
It provides **zero** tamper-evidence — Postgres recomputes it from the same row
on every write, so it cannot disagree with its inputs (§2) — and it is the sole
cause of both defects revision ``20260803_0017`` had to fix: the
``pg_restore``-recompute blocker and the ``extra_float_digits`` leak
(``plans/postgresql-18-migration-rehearsal-2026-08-02.md`` §10.2, §10.2.1). It
becomes a plain column here.

That conversion **silently guts the Monte Carlo receipt unless the procedure is
amended in the same revision.**
``db/agri/procedures/materialize_forecast_iteration.sql:271-280`` INSERTs into
``agri.forecast_iteration_value`` listing only ``iteration_id``, ``valid_time``,
``horizon_step``, ``low_value``, ``median_value``, ``high_value``,
``increment_count`` and ``parameter_checksum``: it relied on the generated
expression to populate ``value_checksum``. After ``DROP EXPRESSION`` every new
row would be NULL. ``db/agri/functions/forecast_iteration_receipt_checksum.sql:44``
then does ``string_agg(value.value_checksum, '|' ORDER BY horizon_step)``, which
returns NULL over all-NULL input, and ``concat_ws`` silently **omits** NULL
arguments — so the receipt digest written at
``materialize_forecast_iteration.sql:304-310`` would stop covering the forecast
values while still producing a well-formed 64-hex string that passes every
retained format CHECK. The procedure is therefore forward-loaded here with the
checksum computed explicitly in that INSERT, per the forward-load workflow in
``db/AGENTS.md`` ("Forward-load workflow", :264-283).

The column is left nullable rather than gaining a NOT NULL: adding one is
outside the agreed scope and would need a full-table validation pass. The
amended procedure is now the only thing that populates it.

**``strategy_selection_quality_evidence`` goes even though the strategy plane stays.**

``db/agri/functions/strategy_selection_quality_evidence.sql:16,29`` INNER JOINs
``agri.v_forecast_hindcast_outcome`` twice, and that view is removed with the
hindcast plane. ``LANGUAGE sql`` and ``plpgsql`` bodies supplied as string
literals are **not** tracked dependencies, so ``DROP VIEW`` would succeed
silently and leave the function as a runtime landmine that raises only when
called. Its only caller, ``finalize_strategy_selection_receipt``, is dropped
here anyway.

**Ordering.**

``agri.finalize_forecast_hindcast_run`` RETURNS ``agri.forecast_hindcast_run``,
a composite type dependency, so it must be dropped **before** the table.
``agri.forecast_hindcast_value`` carried a generated column calling
``agri.forecast_hindcast_value_checksum``, so the table must be dropped
**before** that function. ``agri.v_forecast_hindcast_outcome`` selects from both
tables, so the view goes before them. Every trigger is dropped before the
function it executes. No drop uses ``CASCADE``: a missed dependency must fail
this migration loudly rather than quietly widen its blast radius, which is
exactly the check §7 item 6 of the audit asks for.

**Roles.**

Three of the four locked owner roles are retired:
``plantgeo_forecast_input_recorder_owner``, ``plantgeo_intervention_guard_owner``
and ``plantgeo_release_lineage_guard_owner``.

``plantgeo_forecast_mv_refresh_owner`` is **deliberately not** among them. It
owns ``agri.mv_forecast_ml_daily_serving`` and
``agri.refresh_forecast_ml_daily_serving()``
(``…0015_security_definer_lockdown_completion.py:197-200``). A bare ``DROP ROLE``
would fail ``2BP01``; a ``DROP OWNED BY`` would delete the ML matview.
Non-concurrent ``REFRESH`` requires matview ownership and
``refresh_forecast_ml_daily_serving`` is ``SECURITY DEFINER``
(``db/agri/functions/refresh_forecast_ml_daily_serving.sql:8``), so owner and
definer must remain the same role, and the ``GRANT EXECUTE`` to
``plantgeo_forecast_mv_refresher`` (``…0015:206-218``) must survive —
``routes/health.py:305-309`` asserts it and ``cli.py:429`` exercises it. Nothing
here touches any of that.

``plantgeo_forecast_input_recorder_owner`` owns the seven ``record_*`` functions
that are **kept**, so its teardown is ``REASSIGN OWNED BY … TO CURRENT_USER``
first and only then ``DROP OWNED BY`` (which from that point removes privileges,
not objects). The consequence is a real semantic change worth naming: those
seven functions are ``SECURITY DEFINER`` and now execute as the migrating role
rather than as a locked ``NOLOGIN`` owner. That is accepted — §2 records the
four-role scheme as serving a role model that was rejected and never
provisioned — but it is a privilege widening, not a no-op. They stay
``SECURITY DEFINER`` because the constrained-loader path depends on it: a loader
inserting into ``release_set`` fires a trigger that must write
``forecast_input_recorded_at``.

Roles are cluster-wide while ``REASSIGN OWNED BY`` and ``DROP OWNED BY`` are
database-local. On the rehearsal cluster ``plantgeo_boise_pg18`` is still at
``20260802_0016`` and holds objects owned by all three roles, so ``DROP ROLE``
there raises ``dependent_objects_still_exist``. Each teardown therefore traps
that one condition and raises a NOTICE: the database-local half always
completes, and the role itself disappears once the last database on the cluster
has replayed this revision. Any other error still aborts.

**Two semantic weakenings that no DDL signature reveals.**

Dropping ``ck_forecast_publication_published_evidence``
(``db/agri/tables/forecast_publication.sql:19``) and
``ck_forecast_training_validated_evidence``
(``db/agri/tables/forecast_training_run.sql:32``) lets
``agri.v_forecast_series_serving`` emit rows with NULL
``publication.manifest_checksum`` / ``published_at`` (read at
``v_forecast_series_serving.sql:12-13``) and NULL training checksums (:48-49).
No column becomes nullable in the DDL sense and the view stays valid, but the
served contract weakens: the CLI must now own those invariants.

Flipping ``agri.forecast_iteration.purpose``'s DEFAULT from ``'evaluation_only'``
to ``'serving'`` (and dropping the CHECK that pinned it) changes the receipt
digest for new iterations, because
``forecast_iteration_receipt_checksum`` hashes ``iteration.purpose``. Rows
written before this revision keep ``'evaluation_only'`` and re-derive exactly as
before; rows written after digest ``'serving'``. ``materialize_forecast_iteration``
never names ``purpose`` in its INSERT, so it takes the DEFAULT.

**The one thing phase 7 must not rediscover.**

``agri.finalize_forecast_receipt`` is the **only** object in the schema that
writes ``forecast_receipt.receipt_checksum`` and sets ``status = 'finalized'``
(``db/agri/functions/finalize_forecast_receipt.sql:186-190``); no
``forecast_receipt_checksum()`` function exists. Dropping it is correct and
intended — §3.2 established the finalizer family has zero callers in ``src/`` —
but it means **the ML serving view can never gain a new row** until a Python
publisher byte-exactly reproduces the digest computed at
``finalize_forecast_receipt.sql:153-168`` under the ``20260803_0017``
determinism pins at :9-13. That obligation is pre-existing (the ML lane has
never produced a row) but this revision puts it on the critical path.

**No ``downgrade()``.** This revision destroys objects and evidence; a reversal
would have to invent the dropped hindcast rows and the digests only the dropped
finalizers could compute. Restore a verified backup into a fresh database
instead — the same contract revisions ``20260725_0012`` and
``20260802_0015`` adopted.
"""

from collections.abc import Sequence

from agri_data_service.db.sql_objects import load_object_sql
from alembic import op

revision = "20260803_0018"
down_revision = "20260803_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every object list below is hardcoded rather than derived from the catalogue:
# a revision must apply identically to every database that replays it, so it may
# not branch on the catalogue it finds.

# The 48 governance triggers this revision retires, as (table, trigger). Dropped
# before their functions so each `DROP FUNCTION` can stay CASCADE-free. The 34
# retained triggers -- 6 `guard_forecast_immutable_rows`, 2
# `guard_strategy_review_change`, 16 `record_*`, 10 `require_*` -- are absent by
# design; see the docstring.
GOVERNANCE_TRIGGERS_TO_DROP: tuple[tuple[str, str], ...] = (
    ("analysis_subject", "trg_analysis_subject_immutable"),
    ("artifact", "trg_artifact_intervention_parent"),
    ("forecast_backtest_metric", "forecast_backtest_change_guard"),
    ("forecast_entity_state", "forecast_entity_state_lineage_guard"),
    ("forecast_entity_state", "forecast_entity_state_overlap_guard"),
    ("forecast_feature_snapshot", "forecast_feature_snapshot_terminal_guard"),
    ("forecast_feature_snapshot", "forecast_feature_snapshot_validated_verify"),
    ("forecast_hindcast_run", "forecast_hindcast_finalization_policy_guard"),
    ("forecast_hindcast_run", "forecast_hindcast_finalized_verify"),
    ("forecast_hindcast_run", "forecast_hindcast_insert_contract"),
    ("forecast_hindcast_run", "forecast_hindcast_run_change_guard"),
    ("forecast_hindcast_value", "forecast_hindcast_value_write_guard"),
    ("forecast_iteration", "forecast_iteration_change_guard"),
    ("forecast_iteration", "forecast_iteration_finalized_verify"),
    ("forecast_iteration_actual", "forecast_iteration_actual_change_guard"),
    ("forecast_iteration_actual", "forecast_iteration_actual_lineage_verify"),
    ("forecast_iteration_actual_input", "forecast_iteration_actual_input_change_guard"),
    ("forecast_iteration_actual_input", "forecast_iteration_actual_input_lineage_verify"),
    ("forecast_iteration_value", "forecast_iteration_value_write_guard"),
    ("forecast_observation", "forecast_observation_lineage_guard"),
    ("forecast_publication", "forecast_publication_change_guard"),
    ("forecast_publication", "forecast_publication_published_verify"),
    ("forecast_publication_item", "forecast_publication_item_write_guard"),
    ("forecast_quality_policy", "forecast_quality_policy_finalized_receipt_guard"),
    ("forecast_receipt", "forecast_receipt_change_guard"),
    ("forecast_receipt", "forecast_receipt_finalized_verify"),
    ("forecast_run", "forecast_run_terminal_guard"),
    ("forecast_run", "forecast_run_validated_verify"),
    ("forecast_training_run", "forecast_training_run_terminal_guard"),
    ("forecast_training_run", "forecast_training_run_validated_verify"),
    ("forecast_value", "forecast_value_write_guard"),
    ("intervention_analysis_run", "trg_intervention_analysis_release_set"),
    ("intervention_analysis_run", "trg_intervention_analysis_run_immutable"),
    ("intervention_evidence_input", "trg_derived_evidence_run"),
    ("intervention_evidence_input", "trg_intervention_evidence_input_immutable"),
    ("intervention_evidence_lineage", "trg_intervention_evidence_lineage_immutable"),
    ("intervention_evidence_lineage", "trg_intervention_lineage_release_membership"),
    ("normalized_source_feature", "trg_normalized_feature_artifact_release"),
    ("normalized_source_feature", "trg_normalized_source_feature_immutable"),
    ("release_set", "release_set_identity_freeze"),
    ("release_set", "trg_release_set_intervention_parent"),
    ("release_set_item", "release_set_membership_draft_only"),
    ("release_set_item", "trg_release_set_item_intervention_parent"),
    ("source_release", "trg_source_release_intervention_parent"),
    ("strategy_label_episode", "strategy_label_episode_parent_state"),
    ("strategy_label_release", "strategy_label_release_change_guard"),
    ("strategy_selection_candidate", "strategy_selection_candidate_parent_state"),
    ("strategy_selection_receipt", "strategy_selection_receipt_change_guard"),
)

# The hindcast plane, in dependency order. Written as whole statements rather
# than a name list because the order is the load-bearing part: the finalizer
# RETURNS the run composite, the view selects from both tables, and
# `forecast_hindcast_value` carries a generated column calling
# `forecast_hindcast_value_checksum`. `strategy_selection_quality_evidence`
# rides along because it INNER JOINs the view from an untracked SQL body.
HINDCAST_PLANE_TEARDOWN: tuple[str, ...] = (
    "DROP FUNCTION agri.finalize_forecast_hindcast_run(uuid, character varying);",
    "DROP FUNCTION agri.strategy_selection_quality_evidence(uuid);",
    "DROP FUNCTION agri.forecast_hindcast_signal_timeseries("
    "uuid, uuid, character varying, integer, timestamp with time zone);",
    "DROP FUNCTION agri.forecast_hindcast_receipt_checksum(uuid);",
    "DROP VIEW agri.v_forecast_hindcast_outcome;",
    "DROP TABLE agri.forecast_hindcast_value;",
    "DROP TABLE agri.forecast_hindcast_run;",
    "DROP FUNCTION agri.forecast_hindcast_value_checksum("
    "timestamp with time zone, integer, double precision, double precision, double precision,"
    " double precision, double precision, double precision, uuid, character varying);",
)

# The remaining enforcement families, with full argument-type signatures so a
# name collision cannot silently drop the wrong overload.
# `finalize_forecast_hindcast_run` is absent: it is retired above, ahead of the
# table whose composite type it returns.
ENFORCEMENT_FUNCTIONS_TO_DROP: tuple[str, ...] = (
    "agri.enforce_derived_evidence_run()",
    "agri.enforce_forecast_hindcast_finalization_policy()",
    "agri.enforce_forecast_hindcast_insert_contract()",
    "agri.enforce_forecast_input_lineage()",
    "agri.enforce_intervention_analysis_release_set()",
    "agri.enforce_intervention_lineage_release_membership()",
    "agri.enforce_normalized_feature_artifact_release()",
    "agri.enforce_release_set_freeze()",
    "agri.enforce_release_set_membership_draft()",
    "agri.finalize_forecast_receipt(uuid, character varying)",
    "agri.finalize_strategy_label_release(uuid, character varying)",
    "agri.finalize_strategy_selection_receipt(uuid, character varying)",
    "agri.guard_forecast_backtest_change()",
    "agri.guard_forecast_hindcast_run_change()",
    "agri.guard_forecast_hindcast_value_write()",
    "agri.guard_forecast_iteration_actual_change()",
    "agri.guard_forecast_iteration_change()",
    "agri.guard_forecast_iteration_value_write()",
    "agri.guard_forecast_publication_change()",
    "agri.guard_forecast_publication_item_write()",
    "agri.guard_forecast_quality_policy_change()",
    "agri.guard_forecast_receipt_change()",
    "agri.guard_forecast_terminal_lineage()",
    "agri.guard_forecast_value_write()",
    "agri.guard_strategy_child_insert()",
    "agri.guard_strategy_label_release_change()",
    "agri.guard_strategy_selection_receipt_change()",
    "agri.prevent_forecast_entity_state_overlap()",
    "agri.protect_intervention_evidence_parents()",
    "agri.reject_geospatial_evidence_mutation()",
    "agri.verify_forecast_hindcast_finalization()",
    "agri.verify_forecast_iteration_actual_lineage()",
    "agri.verify_forecast_iteration_finalization()",
    "agri.verify_forecast_publication_transition()",
    "agri.verify_forecast_receipt_finalization()",
    "agri.verify_forecast_validated_transition()",
)

# The nine status-to-checksum evidence CHECKs the state machine no longer needs,
# plus the two CHECKs that pinned `forecast_iteration` to a single method and a
# single purpose. `ck_forecast_receipt_finalized_evidence` is absent on purpose;
# `ck_forecast_hindcast_run_finalized_evidence` leaves with its table.
EVIDENCE_CONSTRAINTS_TO_DROP: tuple[tuple[str, str], ...] = (
    ("forecast_iteration", "ck_forecast_iteration_finalized_evidence"),
    ("forecast_iteration", "ck_forecast_iteration_method"),
    ("forecast_iteration", "ck_forecast_iteration_purpose"),
    ("forecast_publication", "ck_forecast_publication_published_evidence"),
    ("forecast_run", "ck_forecast_run_validated_evidence"),
    ("forecast_training_run", "ck_forecast_training_validated_evidence"),
    ("strategy_label_release", "ck_strategy_label_release_validated_evidence"),
    ("strategy_outcome_definition", "ck_strategy_outcome_definition_review_evidence"),
    ("strategy_selection_policy", "ck_strategy_selection_policy_review_evidence"),
    ("strategy_selection_receipt", "ck_strategy_selection_receipt_finalized_evidence"),
)

# Exactly three of the four locked owner roles. `plantgeo_forecast_mv_refresh_owner`
# is excluded because it owns the ML matview and its SECURITY DEFINER refresher;
# see the docstring.
OWNER_ROLES_TO_RETIRE: tuple[str, ...] = (
    "plantgeo_forecast_input_recorder_owner",
    "plantgeo_intervention_guard_owner",
    "plantgeo_release_lineage_guard_owner",
)

# REASSIGN before DROP OWNED, because `plantgeo_forecast_input_recorder_owner`
# still owns the seven retained `record_*` functions. Roles are cluster-wide but
# both statements are database-local, so a role with objects in a database that
# has not yet replayed this revision survives the DROP with a NOTICE.
_RETIRE_OWNER_ROLE = """
DO $retire_owner_role$
DECLARE
    target_role CONSTANT name := '{role}';
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = target_role
    ) THEN
        RETURN;
    END IF;

    EXECUTE format('REASSIGN OWNED BY %I TO CURRENT_USER', target_role);
    EXECUTE format('DROP OWNED BY %I', target_role);

    BEGIN
        EXECUTE format('DROP ROLE %I', target_role);
    EXCEPTION WHEN dependent_objects_still_exist THEN
        RAISE NOTICE
            '% still owns objects in another database on this cluster; it is '
            'retired here and disappears once every database has replayed '
            '20260803_0018',
            target_role;
    END;
END
$retire_owner_role$;
"""


# The record_* writers were SECURITY DEFINER so a restricted role could append to
# agri.forecast_input_recorded_at without holding INSERT on it. Retiring
# plantgeo_forecast_input_recorder_owner reassigns them to the migrating role, which would
# leave them running as that role — a wider privilege surface than the locked NOLOGIN owner
# they had. The four-role model is rejected and every caller connects with the same
# credentials, so SECURITY DEFINER now buys nothing and is dropped rather than re-pointed.
RECORD_WRITERS_TO_MAKE_INVOKER: tuple[str, ...] = (
    "record_forecast_input_change",
    "record_forecast_release_content_delete",
    "record_forecast_release_content_insert",
    "record_forecast_release_content_update",
    "record_forecast_release_set_item_delete",
    "record_forecast_release_set_item_insert",
    "record_forecast_release_set_item_update",
)


# The retained evidence CHECK was written with a three-valued-logic hole: a NULL
# ``receipt_checksum`` makes the regex NULL, so the conjunction is NULL, the disjunction is
# NULL, and a CHECK rejects only FALSE. A receipt could therefore reach ``status='finalized'``
# with no digest at all, and ``agri.v_forecast_series_serving`` trusts that status. With the
# finalizer triggers gone this CHECK is the only remaining assertion, so it is rebuilt
# NULL-safe here rather than left as the guard everything else is documented to rely on.
_REPAIR_FINALIZED_EVIDENCE_CHECK = """
ALTER TABLE agri.forecast_receipt
    DROP CONSTRAINT ck_forecast_receipt_finalized_evidence;
ALTER TABLE agri.forecast_receipt
    ADD CONSTRAINT ck_forecast_receipt_finalized_evidence CHECK (
        status::text <> 'finalized'::text
        OR (
            receipt_checksum IS NOT NULL
            AND receipt_checksum::text ~ '^[0-9a-f]{64}$'::text
            AND finalized_at IS NOT NULL
        )
    );
"""


def upgrade() -> None:
    # Triggers and constraints drop with IF EXISTS because several of them are created by an
    # earlier revision that loads its DDL from ``db/agri/`` (for example ``20260725_0013`` loads
    # ``triggers/strategy_label_episode.sql``). That tree is regenerated from a dump of head, so
    # once this revision removes an object the earlier revision stops creating it and a bare DROP
    # fails on a chain replayed from scratch. Functions stay strict: their creating revisions now
    # embed their own DDL, so a name that does not resolve is a real error, not a replay artefact.
    for table, trigger in GOVERNANCE_TRIGGERS_TO_DROP:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON agri.{table};")

    for statement in HINDCAST_PLANE_TEARDOWN:
        op.execute(statement)

    for function in ENFORCEMENT_FUNCTIONS_TO_DROP:
        op.execute(f"DROP FUNCTION {function};")

    for table, constraint in EVIDENCE_CONSTRAINTS_TO_DROP:
        op.execute(f"ALTER TABLE agri.{table} DROP CONSTRAINT IF EXISTS {constraint};")

    op.execute("ALTER TABLE agri.forecast_iteration ALTER COLUMN purpose SET DEFAULT 'serving'::character varying;")

    op.execute("ALTER TABLE agri.forecast_iteration_value ALTER COLUMN value_checksum DROP EXPRESSION;")
    op.execute(load_object_sql("procedures/materialize_forecast_iteration.sql", or_replace=True))

    op.execute(_REPAIR_FINALIZED_EVIDENCE_CHECK)

    for function in RECORD_WRITERS_TO_MAKE_INVOKER:
        op.execute(f"ALTER FUNCTION agri.{function}() SECURITY INVOKER;")

    for role in OWNER_ROLES_TO_RETIRE:
        op.execute(_RETIRE_OWNER_ROLE.format(role=role))


def downgrade() -> None:
    raise NotImplementedError(
        "This revision destroys the hindcast plane, the finalizers that alone "
        "could recompute the receipt digests, and three owner roles; restore a "
        "verified backup into a fresh database."
    )
