---
type: track-evidence
slug: environmental_postgres_retirement_20260904
recorded_on: 2026-09-11
status: source_review_and_scoped_verification_passed
---

# Independent environmental repair review

This is the separate verifier pass for the local repair branch based on
`fa202230958fb55521963e886eb031be5fc266c4`. The verifier did not author the
implementation, stage or commit files, deploy, mutate production, start a local
service or database, or run a quality command during source review. Each complete
author correction batch froze before verification; failures were collected across
the wave before any further fixes or affected retries.

## Source review findings

1. **P1 — Recheck the real signal lock owner before each physical mutation.**
   The initial operator checks SQLAlchemy connection flags and wrapper/driver
   identity before each PUT or DELETE, but performs its database round trip only
   before and after the whole synchronous ladder write. A disconnected PostgreSQL
   backend can release advisory locks while those wrapper flags remain unchanged;
   subsequent parts and markers can therefore be written without the claimed
   owner. **Source fix independently accepted:** the ordinary writer runs in a
   worker thread; each mutation marshals bounded real PostgreSQL ownership
   checks onto the owning event loop, with wrapper/driver/session/PID pinning,
   reconnect refusal and fresh availability/retry checks. An active event is
   cleared before owner contexts unwind on cancellation, so the worker cannot
   begin subsequent guarded mutations after its owner exits. An already
   in-flight object-store write cannot be atomically fenced by an advisory lock
   and remains a disclosed limit. Ownership loss, PID drift, head/bootstrap/retry
   drift and cancellation regressions passed in the full Python retry.
2. **P1 — Quoted shell SQL must remain a retirement reader.** The initial new
   shell/PowerShell comment mappings treat any `#` as the start of a comment.
   Executable SQL such as `SELECT payload #>> '{path}' FROM geo.mv_reader_probe`
   can hide its relation from the zero-reader scan. **Source fix independently
   accepted:** shell comments use only a full-line rule, and any quote, backtick
   or heredoc indicator conservatively retains the complete file as executable.
   Regressions cover inline and multiline literals in both operator roots; the
   false-block tradeoff is documented. Regressions passed in the full Python
   retry and final affected retirement run.
3. **P2 — Bound the complete signal day read.** The initial physical scope read
   permits forty objects of 64 MiB each and retains all payloads before validating
   the exact known population. **Source fix independently accepted:** unknown
   keys refuse before GET, each known key uses the maximum of its pinned old/new
   byte lengths, and the whole prepared day is capped at 16 MiB. Ordinary
   readback uses the same pinned per-key limits. Regressions passed in the full
   Python retry.
4. **P2 — Offline MTBS preparation must not install an extension.** The initial
   replay could reach the shared deriver's spatial-extension INSTALL fallback.
   **Source fix independently accepted:** both public older preparation and
   current preservation helpers first open an auto-install/auto-load-disabled
   DuckDB connection and explicitly LOAD the installed extension. A missing
   extension refuses before source/output work. The same preloaded connection
   passes through every older/current rung; normal current-capture callers keep
   their existing defaults. Missing-extension refusal regressions passed in the
   full Python retry.
5. **P2 — FIRMS must write the validated Arrow schema.** The initial preparer
   discarded a validated cast and serialized the Polars table instead. **Source
   fix independently accepted:** all four cast Arrow tables validate before
   output creation and those same tables are written with PyArrow. Footer/schema
   assertions passed in the full Python retry.
6. **P2 — Quoted FIRMS CSV must preserve values.** The initial strict CSV reader
   validated decoded values, then passed original quoted text to a legacy comma
   parser, losing quoted radiometry/confidence while keeping the row count.
   **Source fix independently accepted:** validated decoded cells now feed the
   legacy parser; unsupported comma/newline cells refuse. Raw source bytes stay
   unchanged. The quoted-value regression passed in the full Python retry.
7. **P2 — FIRMS/NWIS numeric validation must match the retained parser.** Python
   accepts numeric text such as `1_0` as ten while the retained JavaScript-style
   parser takes its leading prefix as one. The initial new validation therefore
   allowed a value change with unchanged row counts. **Source fix independently
   accepted:** both preparers now require whole ASCII decimal/exponent text,
   with finite numeric values. Underscore/prefix regressions passed in the full
   Python retry.

Source review of `pipeline/static_soil`, `pipeline/soil_survey_restore`, the older
MTBS capture/recovery modules, their CLIs/SQL/tests and matching preparation
evidence found no further blocking issue beyond the findings above. This is a
code/evidence review, not a test result or production acceptance.

## Evidence limits retained

- The 222-day signal archive is immutable source/candidate evidence. A physical
  correction does not authorize availability publication, reader acceptance or a
  relation drop; it must use the exact archived inputs and all ordinary part
  receipts, with durable originals and a CAS recovery journal before writes.
- The twelve saved SoilGrids assets and pixel results describe a verified local
  candidate. Static estimates retain no observation date or temporal distance.
  Remote admission and reader/agent integration remain separately owed.
- The 959 soil-survey native parts across two roots have no complete production
  source-population or terminal receipt. The bounded local preparer does not
  restore either complete root or establish regional SSURGO completeness.
- The older MTBS candidate contains 3,077 fire identities at every rung; the 747
  current identities remain separately preserved. Catalogue composition,
  same-day collision handling, availability representation, admission and
   production acceptance remain blocked. The historical recovery packet's open
   replay gate is evaluated separately below without rewriting that packet.
- Root operator scripts are reader surfaces. A lexical inventory does not prove
  dynamic SQL absent or supply the other D1 preservation/rollback requirements.
- New FIRMS/NWIS packages prepare only supplied complete-hash source bundles.
  They retain source bytes and refuse malformed/partial supported populations;
  they do not fetch, publish, author absences, merge existing days or prove a
  complete governed AOI. NWIS daily mean support requires explicit reconciliation
  with instantaneous records before admission. The initial restricted-network
  probe returned no bytes; an approved repeat preserved one 11,815-byte public
  response containing seven daily readings. The verifier independently confirmed
  its complete hash and seven sites/series/value blocks/readings, parameter
  `00060`, statistic `00003` and publisher day `2022-08-05`. This exact response
  is neither a complete governed AOI nor a historical candidate.

## Integrated verification

The root froze implementation and review fixes before the first integrated wave.
The final signal test import correction completed while selectors were running,
before any actual quality command began. Both maintained selectors chose their
conservative full fallback:

- Python: `python scripts/check.py --changed --base
  fa202230958fb55521963e886eb031be5fc266c4 --plan`, then the same command without
  `--plan`, from `services/agri-data-service`. New/unmapped product packages and
  scripts select all pytest tests. `receipt_eligible=false` was retained.
- Frontend: `npm run test:changed -- --base
  fa202230958fb55521963e886eb031be5fc266c4 --plan`, then the same command without
  `--plan`. The inherited `.omc/fingerprints.json` was the first unclassified
  fallback trigger; changed SQL also belongs to the shared-contract surface.
- Full frontend static checks: `npm run check:data-boundary`, `npm run
  type-check`, and `npm run lint`.

The Python executable was the existing shared service virtual environment at
`C:/Users/atooz/Programming/plantgeo/services/agri-data-service/.venv/Scripts/python.exe`.
`PYTHONPATH` explicitly selected the current worktree's `services/agri-data-service/src`;
`UV_PROJECT_ENVIRONMENT` selected that virtual environment and the maintained
runner used `uv run --no-sync`. The uv cache was placed inside the worktree.
Rasterio's installed `proj_data` directory supplied `PROJ_DATA` and `PROJ_LIB`.
`AGRI_TEST_DATABASE_URL`, `AGRI_CROSS_MAJOR_DATABASE_URL`,
`PLANTGEO_TEST_DATABASE_URL`, and `POSTGIS_TEST_DSN` were removed from child
environments rather than set to empty strings. No dependencies were installed.

### First integrated wave

| Check | Outcome |
| --- | --- |
| Python formatting | Passed, 0.38 seconds. |
| Python lint | 63 findings across newly authored files, 0.23 seconds. |
| Python mypy | Three findings in three files; 491 source files checked, 45.91 seconds. |
| Python pytest | 5,493 passed, 147 skipped, one xfailed, 639 setup errors; no assertion failures, 125.67 seconds of pytest time. |
| Frontend data boundary | Passed, including 12 URL rules, restricted imports and two fabrication rules over seven roots. |
| Frontend TypeScript | Passed. |
| Frontend ESLint | Passed. |
| Frontend full test fallback | Six tooling tests passed; 144 Vitest files passed and two skipped; 2,151 tests passed and 13 skipped, 153.07 seconds. |

All 639 Python setup errors were the same `PermissionError` on the protected
existing host temporary directory `C:/Users/atooz/AppData/Local/Temp/pytest-of-atooz`.
They did not exercise the affected fixture-dependent test bodies. The remaining
failures were the complete lint batch and three typing issues: explicit bounded
MTBS blob lengths, the MTBS source-ref integer type, and an unused signal import
ignore. The root coordinated one Python-only correction batch after collecting
the complete first wave. Passing frontend gates were retained for the subsequent
Python-only batches. A fresh verified workspace pytest base directory corrected
the environment issue without touching the host temporary directory or harness.

The 13 frontend skips are the four real-PostGIS cases and nine reader SQL cases,
whose explicit database variables were absent. No database coverage is claimed.
The Python skip/xfail details and final affected retry outcome remain below.

The coordinated cleanup was independently source-reviewed after all three
authors froze their files again. It preserved runtime Pydantic imports, exact
prepared values and all source bounds. Forty owned Python/source-test/SQL files
were SHA-256 pinned before the retry. `PYTEST_ADDOPTS` points to a new absolute
`pytest-final` directory below the workspace verification root; its resolved
path and prior nonexistence were checked before invocation. Pytest's environment
argument parsing stripped the unquoted Windows backslashes, creating instead a
new `Usersatooz.codexworktrees2849plantgeo.omcverificationenvironmental-repair-20260911pytest-final`
directory under the service working directory. Its actual resolved path remained
inside this worktree and its creation time was this run. After completion, the
verifier checked both full paths and moved only that generated directory to
`.omc/verification/environmental-repair-20260911/pytest-final-retained` with native
PowerShell literal-path operations. No run was repeated for directory placement.
`TMP` and `TEMP` correctly pointed to a new workspace `runtime-temp-final`
directory. Existing host temporary directories were neither changed nor removed.

### Python retry after the coordinated cleanup

The same maintained Python scoped command ran once with the corrected writable
temporary-directory environment. Formatting passed (0.09 seconds), lint passed
(0.08 seconds), and mypy passed (2.35 seconds). Pytest reported **6,127 passed,
150 skipped, one xfailed and two failures**, with zero setup errors, in 207.06
seconds. The captured database notices name 127 `agri_db`, 13 migration rehearsal
and one cross-major skip; no database integration coverage is implied. The quiet
report does not enumerate the reasons for every other skip or the expected fail.

The two newly exercised failures were collected together:

1. `test_restoration_preserves_native_grain_and_hydric_unknown_at_every_rung`
   reached the complete nongeometry conservation guard and was rejected. The
   shared geometry deriver already restores original column order; exact schema
   conformance and timestamp representation must be resolved without dropping
   fields or weakening value conservation.
2. `test_a_root_without_the_marker_paths_is_refused` inherited the real checkout
   above its new workspace temporary directory. Upward root discovery correctly
   found that checkout, exposing the test fixture's assumption that all temporary
   directories are outside repositories.

The root assigned both fixes together and retained every passing frontend and
unrelated Python result. The verifier accepted the two completed changes before
the affected retry:

- Soil-survey derivation represented two UTC timestamp fields in the local
  DuckDB session timezone. The instants and all other fields were unchanged.
  The product now conforms the derived Arrow table to the registered schema
  before its original strict whole-table nongeometry equality check. The guard
  excludes no additional fields and relaxes no values. Explicit UTC and
  America/Denver regression sessions compare every persisted nongeometry field
  and the exact Arrow schema at all four rungs, including the release date.
- The root-finder fixture temporarily prefixes every required marker with its
  own missing namespace. Real ancestor traversal now verifies a genuinely
  marker-free tree even when the fixture lives inside a checkout. Production
  root discovery and the other real-repository regressions remain intact.

The retry log is `python-check-final.log`; this remains a scoped verification
without a release quality receipt.

### Final affected Python retry

After both authors froze their changes, all full Python static checks passed:
`uv run --no-sync ruff format --check src tests scripts`,
`uv run --no-sync ruff check src tests scripts`, and
`uv run --no-sync mypy src scripts` (491 source files). Then
`uv run --no-sync pytest -q tests/parquet/test_soil_survey_restoration.py
tests/retirement` passed **179 tests with no failures or skips in 2.76 seconds**.
The run began at 19:21:41Z and finished at 19:21:52Z on 2026-09-11.
`PYTEST_ADDOPTS` used the verified, previously nonexistent forward-slash absolute
path ending in `.omc/verification/environmental-repair-20260911/pytest-affected-final`;
the actual fixture path matched it. Forty owned source/test/SQL/evidence-script
hashes were pinned again, with only the three authorized product/test files
different from the preceding full retry. Passing frontend and unrelated Python
tests were not repeated. These layered results are a full fallback followed by
an affected regression pass, not a fresh single all-suite pass on the final tree.
`receipt_eligible=false` remains explicit.

Captured commands and logs live under
`.omc/verification/environmental-repair-20260911`: `python-plan.json`,
`python-check.log`, `python-failure-classification.json`, `frontend-plan.log`,
the four `frontend-*.log` gate logs and `frontend-status.json`. No full-service
quality receipt or production receipt is issued by this scoped invocation.

### Artifact replay and delivery review

The standalone [independent replay script](independent_repair_replay_20260911.py)
ran once after both final fixes, from 19:23:10Z through 19:24:58Z, and exited zero.
Its [compact verification receipt](independent-repair-replay-20260911.json) records
all three verification waves separately, exact commands, source/log pins, replay
identities and limits. The complete 73,521-byte local replay JSON has SHA-256
`f28d2a6a423a69097d96b6bf41766683752a10c225ba06b5506bbf7f01eb485e`.

| Local artifact comparison | Independent result |
| --- | --- |
| Older MTBS | Reconstructed the complete pinned capture and matched preparation SHA-256 `58dbbbd0415cee8b33cd839bdfcefd90a857923b5cd97154990373d0eff003a4`, all 3,077 identities and all four rungs. There are 34 yearly parts per rung, 136 logical part references and 115 unique stored blobs including the manifest. Every unique blob and its full hash matched, totaling 46,457,989 stored bytes; the receipt counts 46,853,605 referenced artifact bytes because some rungs share identical content. |
| Current MTBS rollback | Independently reproduced the pinned current capture, preparation SHA-256 `e50bd78582b4e2bb6ce45ba21188d67309edca25d1d3dc7e6d3a71e49e980406` and every byte of its four 747-row rungs. |
| Signal | Revalidated the complete pinned archive and reproduced offline request SHA-256 `7b9949661c1529650b90e2ea93bc216c27a1312ef6b4ce79af9c463512f5416e`: 222 days from 2025-12-28 through 2026-08-06, 2,212 physical objects, 41,395,777 bytes and all 1,324 ordinary part digests. Logical rows remain 3,506,555 each at z13/z9/z5 and 67,464 at z0. z9 and z5 each have 440 parts, including 218 multipart days; z13 and z0 each have 222 parts. |
| Static soil | Matched all six complete COG hashes (63,503,828 bytes), all four saved point answers and 24 independently read base pixels/scaled values. Selected day is 2026-09-05; observation day and temporal distance remain null for these static estimates. This replay did not repeat the separately recorded PMTiles asset hash pass. |

The replay uses current-worktree copies bound by the
[63-file input custody receipt](preserved-repair-inputs-custody-20260911.json),
with all original pins unchanged. The verifier also independently rehashed all
six copied sensor candidate/provenance files (9,401,169 bytes) against the
[supplemental custody receipt](preserved-sensor-candidate-custody-20260911.json).
That is local custody evidence, not remote archive or admission proof. No replay
command fetched source data, regenerated the historical MTBS recovery packet,
published availability or mutated production. Fresh production signal PREPARE
is separately owned evidence; the request reproduced here remains the archived
offline request. A post-replay hash check confirmed that all 39 owned service
source, test and SQL files still match the final affected-run pins.

The separate [signal PREPARE evidence](signal-sensor-candidate-revalidation-20260911.md)
subsequently completed at 19:29:03Z. The verifier locally rehashed and inspected
the preserved 543,456-byte request (SHA-256
`6d583c672cfb8312e24a75c04cb8bde03aa7d4e2b9852694bfe2daf9cc3fc6ad`)
and 47,880-byte preparation receipt (SHA-256
`79265e40bd5386c6531f1fd23cadb7411315d7e742a3716dda8b17441f6c699d`),
without additional production requests. Every one of its 222 days has two
recorded revalidation passes. All 444 original identities and all 1,324 Parquet
replacement pins match the independently reproduced offline request; all three
recorded operator source hashes match the frozen checkout. Its 2,212 replacement
object pins include timestamped completion metadata. The receipt records 4,446
read calls and 72,830,360 response bytes, zero remote mutations or database
requests, no APPLY and no quiescence proof. Head/bootstrap were absent at three
observations and each of 444 retry/quarantine keys at two observations. These
dated reads require current locked revalidation and external quiescence before
any future physical correction. The local reconciliation result is retained as
`fresh-prepare-independent-document-check.json` under the verification root.

The final [retirement reinventory](retirement-reinventory-20260911.json), captured
at 19:24:02Z, contains 27 blocked relation packets, zero ready packets, 25 present
relations, two absent relations and four lexical zero-reader results. These
counts leave preservation, parity, survival and rollback gates in place; no drop
is authorized.

Final delivery validation passed at 19:33:46Z against the root's explicit
70-file owned-path inventory (SHA-256
`8737c22dd7d903ed881b5e0603bd5fde2815685055b9f04af94cfa77825b98e8`).
All 16 Markdown files passed the local-link check (70 existing links), all eight
owned Conductor Markdown files carry required OKF `type` frontmatter, and all
14 JSON files parse. Direct trailing-whitespace inspection of every owned file
and `git diff --check -- <owned paths>` passed. Final metadata, plan checkboxes
and product verification statements retain the layered test scope and separate
uncompleted production/admission gates. The local audit is
`final-owned-document-check.json` under the verification root. Inherited
Conductor/spec/runtime-rollout records remain outside the authored delivery scope.

### Packaging verification after staging

The original 70-file review above remains the prior delivery pass. Staging then
exposed Git's CRLF-to-LF conversion for five saved JSON receipts, which would
change their cited complete-file hashes after checkout. The root added the
evidence-local [Git attributes](.gitattributes) rule
`*.json -text whitespace=cr-at-eol` and a short custody note in the handoff.
The rule preserves JSON bytes and lets Git recognize their retained CRLF line
endings. The first cached whitespace check flagged only those terminal carriage
returns; the final adjusted cached check passed without changing any receipt.

The independent packaging pass accepted the final **71-file scope**, whose
owned-path manifest has SHA-256
`3053e7f5083b5bd3f9a6465d619a6475bfdc563ef4f7e4d7f20951f80ee6b330`.
The staged path set matched all 71 owned paths with no extra or missing file.
All **14 staged owned JSON blobs matched the workspace bytes exactly**, including
every pinned receipt; all 13 JSON files inside the evidence directory had
effective staged `text=unset` and `whitespace=cr-at-eol` attributes. The staged
attribute file matched its workspace bytes. All 39 owned service source, test
and SQL blobs also matched their verified workspace hashes. The final scoped
`git diff --cached --check -- <owned paths>` exited zero. The raw byte comparison
and initial/final whitespace outcomes are retained in
`final-packaging-staged-json-check.json` under the verification root.

No runtime source, test or JSON receipt changed during this follow-up, and no
runtime test/lint/type gate was repeated. The compact verification receipt
remains SHA-256
`caa9a1e06e0dbc61d274d737038baf2a6a19184498160b9bd9f581155ac51ad6`.
