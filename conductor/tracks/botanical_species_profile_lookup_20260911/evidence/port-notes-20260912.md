# Port notes: botanical species profile lookup, forward from `edc6afd`

Source: `edc6afd` ("feat: add local release-pinned botanical profile lookup",
`codex/botanical-species-profile-lookup`, 96 commits behind `origin/main`). Ported onto
`claude/herbaria-botanical-lanes` (this worktree). No pytest/ruff run — owner rule: authors don't
verify, one sweep runs later. Predictions below are static-read predictions only.

## Materialised verbatim (`git checkout edc6afd -- <path>`)

Source:
- `services/agri-data-service/src/agri_data_service/agent/botanical_species_profiles.py`
- `services/agri-data-service/src/agri_data_service/interface/http/botanical_species_profiles.py`
- `services/agri-data-service/src/agri_data_service/pipeline/direct/botanical_species_profiles/` (`AGENTS.md`, `__init__.py`, `__main__.py`, `publication.py`, `source_ingest.py`, `storage.py`)
- `services/agri-data-service/src/agri_data_service/planes/botanical_species_profiles.py`
- `services/agri-data-service/src/agri_data_service/warehouse/botanical_species_profiles/` (`AGENTS.md`, `__init__.py`, `contract.py`, `reconcile.py`)
- `services/agri-data-service/src/agri_data_service/warehouse/schemas/botanical_species_profile.py`

Tests:
- `services/agri-data-service/tests/botanical_species_profiles/` (`fixtures.py`, `test_contract.py`, `test_publication.py`, `test_source_ingest.py`)
- `services/agri-data-service/tests/interface/test_botanical_species_profile_routes.py`
- `services/agri-data-service/tests/planes/test_botanical_species_profiles.py`
- `services/agri-data-service/tests/test_botanical_species_profile_agent.py`

Conductor evidence (new track directory, taken whole since it doesn't exist on main):
- `conductor/tracks/botanical_species_source_admission_20260911/` (`.gitattributes`, `metadata.json`, `plan.md`, `spec.md`, `evidence/README_WCVP-v16.xlsx`, `evidence/source-admission.json`, `evidence/source-admission.md`, `evidence/wcvp-16-source-release.json`)

Conductor evidence added to the existing `botanical_species_profile_lookup_20260911` track (its own
`metadata.json`/`plan.md`/`spec.md` were deliberately NOT taken — the orchestrator owns those and
main has moved the track on since `edc6afd`):
- `evidence/.gitattributes`, `evidence/changed-files.txt`, `evidence/independent-review.{json,md}`,
  `evidence/integration-receipt.json`, `evidence/integration.md`, `evidence/python-quality-receipt.json`,
  `evidence/railway-census.{json,md}`, `evidence/schema-inventory.md`, `evidence/shared-integration-base.json`,
  `evidence/shared-registration.patch` (the ORIGINAL, stale patch from `edc6afd`; superseded for
  application purposes by `evidence/shared-registration-20260912.patch` below), `evidence/verification.json`,
  `evidence/releases/.gitattributes` and the five `evidence/releases/bspf-0f6b58.../*.parquet` objects
  (the pinned release: `manifest.parquet`, `taxa.parquet`, `assertions.parquet`, `decisions.parquet`,
  `profiles.parquet`).

## Reconciled by hand (files I own)

`tests/direct/test_direct_package_registration.py` and `tests/direct/test_direct_writer_contract.py`
changed shape across the 96 intervening commits (`PENDING_REGISTRATION`'s free-text-exemption design
in the registration test is gone entirely; both files now do strict on-disk enumeration). I applied
the branch's *intent*, not its literal hunk:

- `test_direct_writer_contract.py`: added `"botanical_species_profiles"` to `NON_WRITER_MODULES` with
  the branch's original reason text (`file:87` area) — this file's `NON_WRITER_MODULES` dict still
  exists unchanged in shape, so the hunk applied almost verbatim.
- `test_direct_package_registration.py`: this file lost its `PENDING_REGISTRATION` dict and is now a
  single strict test (`test_every_direct_package_is_registered_without_a_postgres_fallback`) that
  requires every `pipeline/direct/*` package with an `__init__.py` to have a `LANE_REGISTRATIONS`
  adapter. `botanical_species_profiles` is not a scheduled environmental lane and has no adapter, so
  without an escape hatch this test would fail outright. I added a new, analogous
  `EXEMPT_FROM_LANE_REGISTRATION` dict (mirroring `NON_WRITER_MODULES` in the sibling file, which the
  file's own docstring calls out as its sibling) and subtracted it from the on-disk package set before
  the registration check. This is a genuine design addition, not a literal hunk replay — flagging it
  explicitly since the task asked me to keep the branch's design, and the strict-enumeration redesign
  left no smaller way to keep both "no PostgreSQL fallback for scheduled lanes" and "an offline static
  publisher owes no lane adapter" true at once.

## Reconciliation of the ported code against current main

No changes were needed to the ported production/test files themselves — everything they import
still exists on main with the same signature:

- `warehouse/parquet/schema.py::register_stream_schema` / `ParquetStreamSchema` — unchanged; the new
  `warehouse/schemas/botanical_species_profile.py` registers `"botanical-species-profile"`, matching
  the slug-to-module autoload convention documented in `warehouse/schemas/__init__.py`.
- `pipeline/parquet/availability_index.py::BotoAvailabilityStorage.from_settings`,
  `AvailabilityMalformedError`, `AvailabilityUnavailableError`, `StoredAvailabilityObject` — all
  present and unchanged, matching `planes/drought.py`'s object-store/settings usage pattern.
- `parquet_ops/duckdb_session.py::run_bounded_read` and `parquet_ops/faults.py::ServingRefusalError`
  — unchanged, matching the bounded-read pattern the plane uses.
- `interface/http/botanical_species_profiles.py` follows the same Sanic `Blueprint` shape as
  `interface/http/botanical_species_information.py` (own `url_prefix`, refusal-code-to-HTTP-status
  table, `request.app.ctx` storage override hook).

## Serving-is-Parquet-only evidence

`grep -n "sqlalchemy\|models\.species\|AsyncSession\|db_session\|from agri_data_service.db\|from agri_data_service.models"` across `planes/botanical_species_profiles.py`, `interface/http/botanical_species_profiles.py`, `agent/botanical_species_profiles.py`, and all of `pipeline/direct/botanical_species_profiles/*.py` returns **zero matches**. The only relational-shaped surface in the ported code is `pipeline/direct/botanical_species_profiles/source_ingest.py`, which is the offline **publication-time** step (reads the reviewed authoring source, is invoked only by `__main__.py`/`publication.py` to build a new immutable release) — not a serving path. Serving (`planes/botanical_species_profiles.py::read_species_profile`) reads exclusively through `BotoAvailabilityStorage` + `read_release` (Parquet/object-store), confirmed at `planes/botanical_species_profiles.py:214-245`.

## Pending shared registrations (NOT applied — files owned by another agent editing them right now)

`evidence/shared-registration-20260912.patch` in this directory is the regenerated unified diff.
The original `edc6afd` patch (`evidence/shared-registration.patch`, carried forward verbatim per the
task's include list) no longer applies (`git apply --check` fails on `agent/graph.py`, `agent/mcp_server.py`,
`agent/prompts.py`, `agent/tools.py`, `app.py`, `tests/test_agent_graph.py`, `tests/test_agent_provider_wiring.py`
— only `agent/AGENTS.md` still applied cleanly). The regenerated patch:

- **Name collision, load-bearing**: main already defines an unrelated `species_information` tool in
  `agent/tools.py:450` (the existing companion/authoring lookup keyed by `species_id`). The ported
  `agent/botanical_species_profiles.py` also names its tool function `species_information`. The
  regenerated patch imports it under the alias `species_profile_information` and registers that name
  in `WAREHOUSE_TOOLS`, `WAREHOUSE_TOOLS_FOR_WEB`'s exclusion set, and the sufficiency-budget
  denominator in `graph.py` (which already excludes the companion tool's `species_information` by
  name — extended to exclude both).
- `app.py`: register `botanical_species_profiles_bp` in `interface/http/__init__.py`'s `__all__`
  and both the `combined_local` and `published_reader` blueprint groups (current main only has
  `combined_local`/`receiver_writer`/`published_reader`, not `edc6afd`'s older two-profile shape).
- `tests/test_agent_provider_wiring.py`: `WAREHOUSE_TOOL_COUNT` 11 → 12.
- `tests/test_agent_graph.py`: `test_every_tool_statement_is_read_only`'s tool count needs to become
  a filtered count (10 SQL-driven tools; `species_information` and `species_profile_information` both
  execute no SQL and are excused). `test_tool_schemas_publish_bounded_arguments` needs a
  `species_profile_information` branch asserting `{"authority", "authority_version", "taxon_id",
  "release_id"}` are in its schema (distinct from `species_information`'s `{"species_id",
  "companion_limit"}` branch already present on main at `tests/test_agent_graph.py:773-774`).
- `agent/prompts.py`: new "Species profiles are reference evidence" section, referencing
  `species_profile_information` by its aliased name and explicitly telling the model not to
  substitute it for `species_information`'s authoring evidence.
- `agent/mcp_server.py`: `INSTRUCTIONS` gains the profile tool's nonspatial-lookup description under
  its aliased name.
- `agent/AGENTS.md`: documents the alias, the collision reason, and the web/sufficiency exclusion.

None of these six files were touched in this worktree; `git status` confirms only the materialised
paths above are new.

## Predicted test failures (static read, not executed)

1. **All ported botanical-profile tests should import and collect cleanly** — every symbol they
   depend on (`ParquetStreamSchema`, `register_stream_schema`, `BotoAvailabilityStorage`,
   `run_bounded_read`, `ServingRefusalError`, `AvailabilityMalformedError`,
   `AvailabilityUnavailableError`) is present on main unchanged. No failure predicted here.
2. **`tests/test_botanical_species_profile_agent.py`** imports
   `agri_data_service.agent.botanical_species_profiles` directly (not through `tools.py`), so it does
   not depend on the pending alias/registration in `tools.py` — predicted to pass standalone.
3. **`tests/test_agent_graph.py` / `tests/test_agent_provider_wiring.py`** (already-tracked, modified
   by the sibling agent) will fail their tool-count assertions (`WAREHOUSE_TOOL_COUNT`,
   `published_tool_count`, the schema-properties branch) until the pending `tools.py`/`graph.py`
   registration above lands — this is expected and intentionally deferred, not a defect in this port.
4. **`tests/direct/test_direct_package_registration.py`**: without the `EXEMPT_FROM_LANE_REGISTRATION`
   addition I made, `test_every_direct_package_is_registered_without_a_postgres_fallback` would fail
   the moment `pipeline/direct/botanical_species_profiles/` (with its `__init__.py`) exists on disk,
   because it has no `LANE_REGISTRATIONS` adapter. With the addition in place, predicted to pass.
5. **`tests/direct/test_direct_writer_contract.py`**: without the `NON_WRITER_MODULES` addition,
   `test_every_direct_writer_is_in_this_table` would fail the same way (unaccounted module). With the
   addition, predicted to pass; `test_the_one_non_writer_module_really_has_no_parser` also predicted
   to pass since `botanical_species_profiles/__init__.py` exposes no `parser`.
6. **`tests/botanical_species_profiles/test_contract.py`, `test_publication.py`,
   `test_source_ingest.py`, `tests/planes/test_botanical_species_profiles.py`,
   `tests/interface/test_botanical_species_profile_routes.py`**: no reconciliation was needed against
   main for any import they use; predicted to pass standalone. I have not executed them (owner rule),
   so this is a prediction, not a verified result — the one-sweep pass should confirm.
7. **Ruff/lint**: not run (deferred to the end sweep per owner rule); the new `EXEMPT_FROM_LANE_REGISTRATION`
   dict follows the same `Final[dict[str, str]]`-adjacent style as its sibling `NON_WRITER_MODULES`, so no
   stylistic drift is expected, but this is unverified.
