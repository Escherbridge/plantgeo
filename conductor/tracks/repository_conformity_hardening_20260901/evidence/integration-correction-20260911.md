---
type: evidence
slug: repository_conformity_hardening_20260901
wave: integration-correction
date: 2026-09-11
source_revision: cff1144
status: approved
---

# Integration correction validation and review receipt

Integration held `cff1144` for three bounded corrections:

1. distinguish the broad pre-SQL-move sweep from the focused post-move checks;
2. assign the single final combined-tree sweep, quality-receipt regeneration and image proofs to
   integration rather than the offline lane;
3. preserve the historical vegetation simulation/evaluation rule that cutoff and as-of validation
   precedes DSN resolution.

The implementation injects a lazy DSN resolver into the two vegetation workflows. Domain validation
remains in `execution/forecast_workflows.py`; only after those checks pass is configuration
resolved and a session opened. Dedicated tests make any future DSN-first regression fail.

## Shrink s2a and conformity C2 ownership

Shrink s2a had already shipped the `parquet_ops/` package extraction, the grouped
`interface/cli/` public surface, and the hard-cut `agri-service` binary before conformity C2
started. C2 consumed that frozen public boundary; it did not redo s2a or reopen its command-family
contract.

Within the explicit C2 handoff, conformity extracted the six forecast transaction boundaries and
the six shared chunk-lane framework types from `interface/cli/commands.py` into `execution/`, and
moved the four forecast SQL resources beside that execution owner. The strict thin-adapter xfail
now names only the 18 remaining non-forecast transaction sites. Those sites stay retained under
their executor, source-product, and history owners; delegated `interface/cli/data.py`, shared
`parquet_ops/`, `pyproject.toml`, offline-builder, reader, and migration surfaces likewise remain
outside this correction.

No s2a implementation remains owned by the shrink track. Its unfinished s2b direct-writer registry
and forward-execution work is separately delegated to `gapless_parquet_publication_20260901`, while
shrink retains only its later P5/P6 retirement authority. Neither the 18 retained CLI sites nor s2b
is implementation scope for this conformity correction.

## Validation

After the complete correction edit batch, one focused service sweep ran from
`services/agri-data-service/` with `UV_NO_SYNC=1` and `uv run --no-sync`:

```text
ruff format --check forecast_workflows.py commands.py test_forecast_refresh_cli.py
  PASS — 3 files already formatted
ruff check forecast_workflows.py commands.py test_forecast_refresh_cli.py
  PASS — all checks passed
mypy forecast_workflows.py commands.py
  PASS — no issues in 2 source files
pytest test_forecast_refresh_cli.py test_vegetation_ndvi_cli_payloads.py
       test_vegetation_ndvi_release_materialisation.py test_layer_import_contract.py
       test_cli_contract.py
  PASS — 45 passed, 1 intentional thin-CLI xfail in 10.37s
```

The new compatibility tests prove both simulation and evaluation reject an invalid cutoff and a
future as-of time before attempting DSN resolution. This is a proportionate correction receipt,
not the final combined-tree sweep. Integration retains ownership of that sweep, the single
generated `QUALITY_RECEIPT.json`, and both image proofs.

## Independent review

`/root/independent_review`, separate from the correction author, reviewed the completed nine-file
diff without editing it and returned **APPROVE** with no blocking finding. The reviewer confirmed:

- both vegetation workflows validate cutoff and as-of boundaries before invoking the lazy DSN
  resolver, while valid inputs open the same session and transaction;
- Click only injects dependencies, so business validation remains in `execution/`;
- the four precedence cases and affected contract tests adequately lock the regression;
- the layered validation record does not claim a final combined-tree receipt;
- s2a, s2b, conformity C2 and the 18 retained transaction sites are assigned to their actual
  owners; and
- no frozen reader, builder, registry, direct-writer, migration, receipt, image or production
  configuration surface changed.

The attributable dimension ratings were Security 5/5, Correctness 5/5, Performance 5/5,
Maintainability 4.5/5, Test coverage 4.5/5, and Ownership/governance 5/5. This approval covers only
the bounded correction; it does not replace integration's final combined-tree acceptance.

## Retained evidence locations

- This correction validation/review receipt:
  `evidence/integration-correction-20260911.md`.
- The bounded implementation inventory and pre-correction layered results:
  `evidence/final-inventory-20260911.md`.
- The executable precedence regression cases:
  `services/agri-data-service/tests/test_forecast_refresh_cli.py`.
- The prior bounded implementation is Git revision `cff1144`; the correction revision is supplied
  to integration in the commit handoff after Git assigns it.

## Scope and rollback

The correction edits only the two vegetation workflow seams, their focused test and local
directory contracts, plus this track's plan, metadata and evidence. Rollback is the single bounded
correction commit. No database, migration, service, deployment, image push or production state is
read or changed.
