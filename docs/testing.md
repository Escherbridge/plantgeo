# Testing by change surface

Apply the complete change batch, then run the appropriate checks once. Use scoped tests
for development; keep full type checking, lint, and data-boundary checks when the application
changes. Shared infrastructure changes and production image builds retain the full suite.

## Frontend and Next.js

| Command | Purpose |
| --- | --- |
| `npm run test:changed -- --plan` | Show selection and commands for staged, unstaged, and untracked changes against HEAD |
| `npm run test:changed` | Run tests affected by the current working changes |
| `npm run test:changed -- --base main` | Include committed branch changes from the merge base, plus working changes |
| `npm run test:batch -- --list-batches` | List available named batches |
| `npm run test:batch -- --batch ai` | Run the AI batch and filesystem contract tests |
| `npm run test:batch -- --batch parquet --batch map` | Run the union of two batches in one Vitest process |
| `npm test` | Run test-routing checks and the full Vitest suite |

Named batches are `ai`, `parquet`, `map`, `identity`, `api`, `offline`, `contracts`, and
`full`. These are explicit investigation scopes; use change selection to follow dependencies
across domain boundaries. `--plan` can be added to either selection mode without executing tests.

Change selection first passes existing application files to Vitest's `related` command, which
traverses static and resolvable dynamic imports. This source-only batch must pass before a
second batch runs filesystem and command contracts. Finding no source-related tests fails;
unrelated contracts cannot hide that result. The two batches incur two Vitest startups and
can overlap, but usually run fewer tests than the full suite. Use the full batch when the
dependency graph cannot resolve the changed surface.

Selection follows the aggregate changed surface; it is not a per-file coverage measurement.
Review new code for an appropriate behavior test even when other changed files already select
tests. A passing batch does not establish that every changed line has coverage.

Schema, migration, source and shared fixture assertions can be invisible to the import graph.
New filesystem-driven test helpers must be included in this selection
contract or treated as shared infrastructure. Computed imports and external runtime
dependencies require the full batch.

Package/configuration changes, test harness changes, database code, deployment infrastructure,
deleted files, non-module assets, and unclassified runtime files select the full suite. Missing Git references
fail instead of silently selecting nothing. Documentation-only changes skip frontend tests.
Python source is checked by the Python runner; shared JSON/SQL data contracts select the full
frontend suite. A scoped pass records the requested surface; it does not certify a release.

For application changes, the usual final local batch is `npm run check:data-boundary`,
`npm run type-check`, `npm run lint`, and `npm run test:changed -- --base <review-base>`.
When changing the test runner itself, use `npm test` for the final integrated check.
Railway image builds still run the full checks because their source context does not carry
a verified Git comparison base. Do not substitute an empty or guessed diff there.

## Python data service

From `services/agri-data-service`, use `python scripts/check.py --changed --plan` to inspect
the pytest selection, then `python scripts/check.py --changed` to execute it. Add `--base REF`
to include branch commits. Use `--list-batches` to discover names and repeat `--batch NAME`
to combine related surfaces. Formatting, lint and type checking remain integrated.

The default `python scripts/check.py` retains the full sweep. Scoped requests cannot write a
full quality receipt, even if a conservative fallback selected every test. Release receipt
creation still requires the full receipt-producing invocation. Database-dependent tests
remain explicit integration checks; no local database is required or started by selection.
For a run without a test database, remove `AGRI_TEST_DATABASE_URL`,
`AGRI_CROSS_MAJOR_DATABASE_URL`, `PLANTGEO_TEST_DATABASE_URL`, and `POSTGIS_TEST_DSN`
from the child process environment. Do not set them to empty strings: existing harnesses
distinguish an absent variable from a configured one.

## Keeping tests useful

Remove a test when its only subject is code that has been proven retired. Preserve the
historical contract and retirement evidence in Conductor, with a link to the canonical live
replacement. Keep tests for live boundaries, recovery behavior, data quality, authorization,
and previously observed failures. Test count by itself is not a quality target.
