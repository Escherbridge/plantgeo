# Vegetation production proof scripts

`purge_parquet_layout.py` is permanently inventory-only. Its legacy `--confirm` spelling is a
fail-closed compatibility guard that exits before object-store construction; no script in this
directory may treat that flag or `--include-unparsable` as deletion authorization.

`vegetation_ingest_status.py` reads the raw `geo.features` vegetation population in a read-only
transaction. Its changed-cell-day counts collapse raw scene rows to the same `(cellKey,
publisher-named day)` grain the promotion transform uses, then test presence against the governed
NDVI source filters. Empty populations keep nullable bounds rather than turning an operational
absence into an exception.

`vegetation_source_inventory.py` brackets the object-store scan with independent read-only governed
source censuses. A changing source makes the report non-clean. Schema classification is deliberately
closed: only the exact current schema and the pinned coordinate-less predecessor have names;
anything else is `unknown`, and unknown/read-error state exits nonzero so its day cannot become a
destructive rewrite manifest by implication.

# Weather observations exact audit

`audit_weather_observations_exact.py` is the credential-free current-weather completion proof. Run
it from this service directory; it starts PostgreSQL in read-only mode, excludes every object day
before the weather registry floor, and compares the governed settled window through all four zoom
tiers. Its object-plane walk is bracketed by independent repeatable-read source snapshots, whole-key
inventories, and canonical object/marker rereads; the report cannot be clean if either plane moves
during reconciliation. `--output` persists the credential-free full JSON report locally. A non-clean
report exits nonzero and never repairs, marks, prunes, or writes anything.

# Soil-wetness canonical breakdown

`soil_wetness_snapshot_breakdown.py` consumes one manifest-pinned raw canonical signal snapshot and
never opens PostgreSQL. Its three exact product filters are GWETTOP, GWETROOT and GWETPROF; their
outputs live under distinct `derived-canonical/` lane prefixes, outside the mutable `layer=signal`
namespace. Each bounded UTC-month step verifies the referenced raw part receipts, classifies every
physical row, writes a full provenance sidecar and writes deduplicated z13 day parts. Release
precedence is the governed signal rule: newest frozen source-release retrieval, then greatest
physical observation id.

All writes are conditional and byte-checked. Month checkpoints land after their parts, z9/z5/z0
derive only from the completed z13 bytes, every day-tier marker lands after its part, and the z13
marker lands after the coarse ladder. A lane manifest is published only after exact input,
provenance, winner and tier reconciliation; `_COMPLETE` is last and pins the manifest SHA-256.
Receipt verification uses bounded concurrent reads within one month and publishes a deterministic
month marker only after every object passes its byte, SHA, schema and row checks. Those markers bind
the canonical checkpoint bytes and are themselves digest-bound by the lane manifest, so an
interrupted finalizer resumes without weakening or repeating completed verification.

`soil_wetness_snapshot_audit.py` is the separate read-only publication auditor. Its store wrapper has
no write method. It ignores verification-marker shortcuts, re-reads every receipt, recomputes each
month from the pinned raw parts, reproduces provenance and z13/z9/z5/z0 bytes, and requires exact lane
and bundle inventories plus the manifest/completion SHA chain.

# The gate and its locked quality receipt

`check.py` is the single authority for the four Python gates. As of 2026-09-02 it runs
`ruff format --check src tests scripts`, `ruff check src tests scripts`, **`mypy src scripts`** and
`pytest -q`, and `Makefile`'s `lint` / `format` / `test` targets name the same three paths.

**Every child runs `uv run --no-sync`, and so must you.** A bare `uv run` re-resolves the
environment from the lock's default groups, which drops the `dev` extra and takes pytest, ruff and
mypy with it in the middle of a sweep -- the failure then reads as a code failure, not a tooling
one. `make install` (`uv sync --locked --all-extras`) is the only sanctioned sync.

## Why `mypy` now covers this directory

It was `mypy src` until 2026-09-02, so every operator script here was unchecked. Turning it on
surfaced 204 findings in 18 files, including one script that could not import at all:
`build_soil_moisture_from_canonical_snapshot.py` imported
`agri_data_service.warehouse.schemas.soil_field_moisture`, a module that was split into three
per-depth modules without re-homing its `SOIL_FIELD_MOISTURE_STREAMS` mapping. Three AST tests in
`tests/test_snapshot_builder_contracts.py` assert against that script's constants and never execute
it, so nothing caught it.

The scripts' JSON documents -- manifests, ledgers, checkpoints, receipts -- are typed
`Mapping[str, Any]` at the decode boundary and narrowed with explicit guards or a named helper
(`_require_int`, `_require_frame`) before any value drives a decision. They are deliberately *not*
`TypedDict`: these documents are mutated in place and their bytes are SHA-256 pinned into published
receipts, so a restructure that satisfied a `TypedDict` would change the emitted bytes. See
`conductor/code_styleguides/python.md`, "Baseline", for the reconciled rule.

## `QUALITY_RECEIPT.json`

`uv run --no-sync python scripts/check.py --write-receipt` writes
`services/agri-data-service/QUALITY_RECEIPT.json` **after** a green run of all four gates. It
refuses to write in four situations, one test apiece in `tests/scripts/test_check_receipt_guards.py`:

1. **`--only`.** A partial sweep judged part of the tree, so it may not certify all of it. Refused
   before the sweep starts, not after three minutes of it.
2. **A red sweep.** The receipt's entire claim is that every gate passed.
3. **A tree that moved while the sweep ran.** The digest is taken *before* the checks and again
   after; differing digests mean a file was written during those minutes and no gate read it.
4. **A tree that disagrees with git.** See "The receipt is a claim about committed bytes" below.

The receipt records:

- `tree_digest` -- one sha256 over every file under `src/**`, `tests/**`, `scripts/**`,
  `alembic/**` and `db/**`, plus `pyproject.toml`, `uv.lock`, `mypy.ini`, `ruff.toml` and
  `alembic.ini`, sorted by POSIX-relative path, with the path and the content each length-prefixed
  so a rename can never digest the same as an edit. `__pycache__`, `*.pyc/pyo/pyd` and the tool
  cache directories are excluded because they differ between a developer tree and a Docker build
  context. Domain-separated by a constant prefix (`DIGEST_DOMAIN`, currently `v2`). Measured
  2026-09-03: **1,124** files (561 `src`, 257 `tests`, 241 `db`, 33 `alembic`, 27 `scripts`, 5 root
  files); `digest_file_count` in the receipt is always the authoritative number.

  Why those roots. `src` and `tests` are what pytest and mypy judge, and `scripts` is the operator
  surface the extended mypy scope covers. `alembic/` and `db/` are inputs because the runtime image
  **ships** them (`Dockerfile:49-51`): while they sat outside the digest, a migration edit shipped
  under a receipt that still verified. `mypy.ini` and `ruff.toml` are inputs because they define
  what "mypy pass" and "lint pass" mean -- loosening a rule there silently redefines the judgement
  the receipt records. `alembic.ini` names the script location those migrations run from.

  Content is CRLF-normalized to LF before it is length-prefixed and hashed. A Windows working tree
  may carry CRLF: `.gitattributes`' `* text=auto eol=lf` governs what git writes on `add` and on
  checkout, **not** the bytes already sitting on disk, so the normalization -- not the attribute --
  is what makes the digest reproducible across platforms. Hashing raw disk bytes made a receipt
  written on a Windows checkout un-verifiable inside the Railway build, which reads a fresh checkout
  of the same commit: measured 2026-09-03, 181 of 842 digest inputs differed only by line ending and
  the two digests disagreed while `git diff` showed no changes. The trade-off is that two files
  differing only in CR bytes now digest equal. For an input git detects as text that is unreachable,
  because git refuses to store the difference; it becomes reachable the day a `binary`-attributed
  file lands under a digest directory (the root `.gitattributes` marks `*.parquet`, `*.png`, `*.zip`,
  `*.gz`, `*.pbf` and `*.woff*` binary). None of today's 1,124 inputs is one: they are 629 `.py`,
  440 `.sql`, 33 `.md`, 12 `.json`, 2 `.ini`, 2 `.toml`, 1 `.lock`, 1 `.mako`, 1 `.typed`, 1 `.js`
  and 2 extensionless. A lone `\r` is deliberately left alone, which is what git does too.

  A receipt written before the normalization landed was domain-separated as `v1` and can never
  verify against the `v2` digest function; it must be rewritten by a green sweep, never hand-edited.
- `digest_domain` -- the digest function's own name, copied from `DIGEST_DOMAIN`. A receipt written
  by an older algorithm then fails as "written with digest domain X, this verifier computes Y",
  which names its own remedy, rather than as "source changed", which sends an operator to re-run a
  sweep that cannot help. `schema_version` is **2** because that key is required.
- `digest_file_count`, `generated_at`, the `python`/`uv`/`ruff`/`mypy`/`pytest` versions that
  produced the judgement, and each gate's command, status and duration.

### The receipt is a claim about committed bytes

A receipt describes a tree no one else can see unless git has it. So before writing, `check.py`
reads git's index for the digest inputs -- `git ls-files -s -z` for the staged blob ids, one
`git cat-file --batch` for their bytes (one batch, not a thousand processes), and two
`git ls-files --others` listings -- and digests those blobs with the same function it ran over the
disk. It refuses, naming every offending path, when a digest input is:

| state | why a fresh checkout would differ | fix |
| --- | --- | --- |
| untracked | your digest has a file no checkout will have | `git add <path>` |
| edited since staging | your digest has bytes git does not have | `git add <path>` |
| staged but deleted on disk | the checkout still carries the file | `git add <path>` |
| ignored | no commit can ever carry it | un-ignore it, or delete it |

**Consequence for the workflow: stage new files before `--write-receipt`.** A new test module left
untracked used to produce a green receipt that then failed every image build with an unexplained
digest mismatch; it now produces a refusal that names the file. Committing is not required, staging
is -- the index is what the digest is compared against. Paths the digest does not cover are filtered
out by name first, so the hundreds of ignored `__pycache__` entries under `db/` and `alembic/` say
nothing.

The verifier stays git-free, because a Docker build stage has no repository. Proving the receipt
describes committed bytes is therefore the writer's job alone, and only the writer can do it.

### What the verifier says when it refuses

`scripts/verify_quality_receipt.py` recomputes the digest, checks every recorded gate passed, and
exits non-zero on any mismatch. Its digest-mismatch message lists the three causes in the order they
occur -- (1) an input edited after the sweep, (2) a receipt committed without a new or changed input,
(3) an input excluded by `.gitignore` or `.dockerignore` -- each with its own fix, because only the
first is repaired by re-running the sweep. A differing file count points at 2 or 3; an equal count
points at 1. Stale `schema_version` and stale `digest_domain` are separate messages, so a receipt
from an older algorithm never masquerades as changed source.

It is stdlib-only on purpose: the image build runs it on a bare interpreter with no virtualenv. Both
`services/agri-data-service/Dockerfile` and `infra/job-executor/Dockerfile` run it in a dedicated
`quality-receipt` stage and then `COPY --from=quality-receipt` the verified receipt into the runtime
image -- **the copy is what forces the stage to build**, because BuildKit prunes an unreferenced
stage and a pruned gate is not a gate. Every digest input must be COPYed into that stage or the
digest cannot reproduce; `tests/` and `scripts/` stay there and never reach a runtime image, since a
deleting `RUN` would not shrink an already-written layer. `alembic/` and `db/` are copied into both
the gate stage and the agri runtime stage on purpose -- the executor image copies them into its gate
stage only, and its runtime deliberately ships no migration machinery.

Editing source without re-running the sweep therefore fails the **build**, not the first request.
The practical consequence: any change to `pyproject.toml`, `uv.lock`, `mypy.ini`, `ruff.toml`,
`alembic/**` or `db/**` -- a dependency removal or a new migration, for instance -- must be followed
by `--write-receipt` or both images stop building.

This is the 2026-09-01 audit's "locked quality receipt before deployment" row. It is deliberately
NOT the in-image `checks` stage that the 2026-08-07 owner ruling dropped: it re-runs nothing (a
Docker build has no disposable PostgreSQL, so `pytest` could never run there), it only proves the
tree still equals the one a green sweep judged.

### Current state and the one command that refreshes it

`QUALITY_RECEIPT.json` **exists** and is written by a green sweep; the 2026-09-03 sweep recorded all
four gates passing (`pytest -q` in 174s) over the then-current inputs. Any change to a digest input
-- including the ones that just joined it -- invalidates it, and both images stop building until it
is rewritten:

```bash
cd services/agri-data-service
git add <every new or edited digest input>                 # the writer refuses an unstaged tree
uv run --no-sync python scripts/check.py --write-receipt   # refuses unless all four gates are green
uv run --no-sync python scripts/verify_quality_receipt.py  # must exit 0
```

### 2026-09-03: a Windows receipt that could not verify on Linux

Both Python images failed to build from the `e4a101f` push at the `quality-receipt` stage. The
receipt had been written on a Windows checkout where 181 of 842 digest inputs carried CRLF, so the
Linux build context could never reproduce the recorded digest (`3824cf2c` recorded, `b0ec4347`
computed by Railway; reproduced locally with `git archive HEAD`). `1da1a28` moved the digest to
CRLF-normalized bytes and the domain to `v2`, and an independent review then reproduced the fixed
digest from a Linux clone. The follow-up wave added what would have caught it before the push: the
index comparison above refuses a receipt whose inputs git does not hold, and `digest_domain` turns a
future algorithm change into its own message instead of a false "source changed".

**The TypeScript side has no equivalent yet.** There is no receipt over the root `src/**`,
`package.json` or `package-lock.json`, and the root `Dockerfile` verifies nothing about whether its
sources were linted or tested. A Python-only receipt is a real gate over a real half of the tree,
not a whole-repository guarantee; treat it as such until the frontend has its own.

# Canonical snapshots and immutable lane breakdowns

## Lint boundary for exact offline workflows

The named snapshot, breakdown, reconciliation, and cutover scripts have narrow per-file Ruff
exceptions in `../ruff.toml` for complexity, argument-count, and contract-literal rules. The
soil-moisture builder additionally permits its explicit module-state mutation because one process
selects exactly one depth profile before doing any work. These are finite, run-once audit workflows
whose explicit branches mirror receipt states and whose numeric literals bind immutable source/tier
contracts. Correctness rules, undefined names, closure binding, typing, formatting, and unused data
remain enforced; do not broaden the exceptions to a directory wildcard or reuse them for runtime
ingestion code.

## Canonical signal snapshot

`canonical_signal_snapshot.py` is a one-time, resumable migration/export tool, not a serving-lane
writer. Its namespace is deliberately outside `layer=...`:

```
raw-canonical/signal-observation/snapshot=<snapshot-id>/
```

The snapshot is cut at an immutable `signal_observation.id` high-watermark. PostgreSQL has no index
leading on `observed_at`, so the tool first enumerates `spatial_cell.id` through the primary key and
then reads each UTC month through bounded cell batches. Each batch is grouped and written independently;
the durable resume unit is `(UTC month, deterministic cell-batch index)`, so
neither extraction nor verification holds a full month in memory. Signal and UTC observation day
remain sorted columns for Parquet row-group pruning; making both path axes would create a pathological
object count. Each observation read disables sequential scans inside its read-only transaction because production probes
showed that dispersed cell batches can otherwise flip to a full scan of the 11 GB heap.

Part keys and checkpoint keys are deterministic and immutable. A retry may accept an existing object
only when its bytes match exactly. Each month/cell-batch ledger is written after every part for that
unit. The final manifest is built from those ledgers only after a second PostgreSQL-to-Parquet
reconciliation, and `_COMPLETE` is the last object written.

The high-watermark bounds inserts, but PostgreSQL observations and the minimal dimension fields copied
into each fact must remain unchanged until verification finishes. If an older fact changes during a
run, immutable-part or row-digest reconciliation fails closed; resume under a new snapshot id after
the source is stable.

The raw contract keeps every physical observation row. It performs no governed filtering,
deduplication, precedence, or lane-specific aggregation. `product=` is the exact `source_parameter`,
and `source=` is the governed `data_source.key`; both exact values also remain columns in each file.
The physical layout is `source/product/support/year/month`, with rows sorted by signal, product,
observation instant, cell, release, and physical observation id. The fact repeats only the original
19 observation fields, partition/join keys, and stable cell identity/centroid. Full physical rows for
`data_source`, `source_release`, and `spatial_cell` are immutable companion Parquet objects under
`_dimensions/`; JSONB is canonical PostgreSQL text and PostGIS geometry is EWKB. These dimensions
are captured immediately after the signal high-watermark. Verification reconciles them against that
start-time descriptor rather than the later live tables, so an unrelated dimension row inserted while
the export runs cannot poison the snapshot.

## Read-only signal census

`census_signal_snapshot.py` is the read-only contract probe for
`prod-20260826-full-signal-v1`. It pins the manifest byte digest, binds every one of the 424 ledger
bodies to its manifest summary, classifies all product/support populations, and can prove the three
ERA5-Land moisture lane audits are pairwise disjoint and conserved against the whole snapshot.
NASA POWER GWET populations are explicitly outside this task's ownership.

## VPD

`vpd_snapshot_breakdown.py` is the sole writer for the immutable
`layer=soil-field-vpd/snapshot=prod-20260826-full-signal-v1/` product. Its only input is
`raw-canonical/signal-observation/snapshot=prod-20260826-full-signal-v1/`, and the source manifest
must hash to `465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f`.

The census follows the manifest's 424 bounded month/cell-batch ledgers. The build copies every VPD
source part byte-for-byte into `kind=physical`, records the complete source-part ledger lineage in
monthly checkpoints, classifies every physical row, and applies newest-release/highest-observation
precedence only when producing `kind=observed`. Monthly observed parts are written for z13, z9, z5,
and z0. Writes are immutable: an existing different payload is a hard conflict. `manifest.json` and
`_COMPLETE` are written only after all monthly checkpoints reconcile, and verification binds them
back to the pinned canonical census.

This script has no PostgreSQL, publisher, legacy `layer=signal`, API, client, or deletion surface.

## Air temperature

`air_temperature_snapshot_breakdown.py` is bound to one completed raw-canonical snapshot and its
root-manifest SHA-256. Its census reads only the root manifest and the finite ledger set named by it;
it never discovers source parts by an unbounded prefix walk. The three product contracts are
non-overlapping at the `(source_parameter, signal_name)` boundary, and any Air Temperature-shaped
part that misses a source, unit, support, grid, or parameter/signal constraint is an explicit
exclusion instead of silently falling out of the reconciliation.

Verification always rereads every immutable monthly checkpoint and reconciles its complete lineage
and row counts. The default sparse mode then hashes five evenly spaced months per product, including
all four zoom rungs and the boundary/middle physical objects for each sampled month. Full payload
verification remains an explicit mode for offline audits, not the normal completion path.

## Dew point

`dew_point_snapshot_breakdown.py` is the single-product companion to the Air Temperature snapshot
builder. It reads only the same manifest-pinned raw canonical snapshot, owns exactly the governed
`nasa-power-daily / T2MDEW / dew_point_temperature / surface / C` population, and writes only the
dedicated `layer=climate-field-dew-point/snapshot=prod-20260826-full-signal-v1/` prefix. Every
physical source part is retained with exact lineage; newest frozen release retrieval and then the
greatest observation id select downstream winners. Monthly z13/z9/z5/z0 outputs and checkpoints are
immutable and resumable, and `_COMPLETE` lands only after the reconciled manifest.

The `census` command is the mandatory no-write preflight. Production `build` must not be invoked
until its exact source-part/row/byte counts and proposed destination have been independently reviewed.

## Relative humidity

`build_relative_humidity_from_canonical_snapshot.py` is the sole writer for the historical
`climate-field-relative-humidity` breakdown. It has no database surface: its only input is canonical
snapshot `prod-20260826-full-signal-v1`, and it refuses unless the source manifest bytes hash to
`465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f`. The legacy `layer=signal`
prefix is never an input or output.

All destination objects live under the non-live immutable namespace
`layer=climate-field-relative-humidity/snapshot=prod-20260826-full-signal-v1/`. The builder never
lists, writes, retracts, or interprets the live lane prefix outside that snapshot root.

The script follows the source manifest's month/cell-batch ledgers and holds one month at a time.
Every declared relative-humidity source part is checked by byte count, SHA-256, row count, and canonical row digest.
Rows are then classified into the governed `RH2M` / `relative_humidity` population or one mutually
exclusive exclusion reason. Within the governed grain, the winner is the newest source-release
retrieval timestamp and then highest physical observation ID. No physical row is discarded:
precedence-ordered lineage arrays retain every ID, canonical row hash, release ID, source-part
checksum, and row ordinal, while the selected fields repeat lineage element zero for direct audit.

Destination writes are conditional create-only operations. Existing bytes are accepted only when
identical; no key is overwritten, retracted, or pruned. A month checkpoint is written only after
all four day tiers and their completion markers read back correctly. The final manifest binds all
checkpoint hashes. A rerun verifies completed months and continues at the first absent checkpoint.

The canonical source manifest records each month ledger's identity, totals, and source-row digest,
but not the ledger object's byte checksum. The source-chain audit therefore proves every constructed
ledger has the exact manifest summary, enforces every selected part's exact snapshot-root path,
verifies its bytes and row digest, and records the ledger byte SHA-256 as consumed. The immutable
`source-chain-audit.json` and `_AUDIT_COMPLETE` are written before the destination manifest; that
manifest binds both hashes, and the destination `_COMPLETE` is the final write. The audit runs on
resume too, so a completed destination never skips revalidation of its source receipts.

Coarse z09/z05/z00 rows derive directly from each z13 day. Row-level winner provenance becomes null
after aggregation because a coarse cell has no single physical source row; each day checkpoint
instead binds every derived object to the exact z13 part key and SHA-256 from which it was derived.

## Solar radiation

`build_shortwave_radiation_from_canonical_snapshot.py` is the sole historical writer for
`climate-field-shortwave-radiation`. Its only data input is raw-canonical snapshot
`prod-20260826-full-signal-v1`; it refuses source bytes whose manifest SHA-256 is not
`465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f`. PostgreSQL, publishers,
the legacy `layer=signal` prefix, and serving surfaces are outside this script's contract.

The reader follows all 424 pinned month/cell-batch ledgers. It checksum- and row-digest-verifies
the 400 present `nasa-power-daily/ALLSKY_SFC_SW_DWN/surface` parts and records the 24 missing parts
as explicit ledger absences: every batch in June, July, and August 2026. Every physical row is
classified exactly once. The governed population has 1,166,676 eligible rows and no row-level
exclusions.

Release precedence is downstream only: newest `source_release.retrieved_at`, then highest physical
observation ID. The selected z13 row retains precedence-ordered arrays containing every physical
ID, canonical row hash, release ID, source-part key/checksum, and row ordinal, so dedup does not
erase source lineage. The exact base census is 592,721 rows: 1,493 continuous days from 2022-04-30
through 2026-05-31 on the 397-cell `nasa-power-0.5-degree` lattice.

Writes are conditional create-only. One bounded month is held at a time; each month checkpoint is
written only after z13/z09/z05/z00 parts and day completion markers read back correctly. Source
audit objects are published after all checkpoints. The destination manifest binds the audit and
checkpoint hashes, and the destination `_COMPLETE` marker is the final write. A rerun accepts an
existing object only when its bytes are identical and resumes from immutable checkpoints.

## ERA5-Land soil moisture

`build_soil_moisture_from_canonical_snapshot.py` writes exactly one selected ERA5-Land depth lane
per invocation. It never queries PostgreSQL or calls an upstream publisher. Source parts are
verified from canonical ledgers, release precedence is applied only after physical-row accounting,
and every winning row retains its ordered source-part/row lineage. Daily z13/z9/z5/z0 objects and
completion markers are immutable; monthly checkpoints make interruption resumable. The source
audit is written before the destination manifest, and `_COMPLETE` is the final write.

## ERA5-Land soil temperature

`soil_temperature_snapshot_breakdown.py` is the isolated, manifest-pinned breakdown for the four
ERA5-Land soil-temperature depths. It reads only
`raw-canonical/signal-observation/snapshot=prod-20260826-full-signal-v1/`, whose manifest SHA-256 is
fixed in the script. The four canonical product partitions are pairwise disjoint and land under
separate `derived-canonical/signal-observation/lane=<depth>/snapshot=<snapshot>` roots; the script
does not open PostgreSQL, call a publisher, or touch `layer=signal`.

The 424 monthly cell-batch ledgers are the only fact inventory. All 8,364 part descriptors must
reconcile to the pinned manifest and are classified once as an included depth or an explicit
descriptor-scope exclusion. Each selected source part is byte-, schema-, row-, and digest-checked
before use. Every physical fact is retained in monthly provenance with its
source-part key, source-part SHA-256, source row ordinal, frozen release metadata, and explicit
disposition. Invalid contract rows remain classified in provenance/checkpoints and prevent lane
publication. Release precedence is applied only to the downstream winner grain, which includes
data source, source parameter/depth, support, signal, unit, cell, and day.

Before any lane write, the builder reads all fact parts one ledger at a time, using at most eight
concurrent part reads inside that ledger. It verifies every physical canonical hash and recomputes
the ledger `source_row_digest` over the complete ledger row set. Resume does not trust an existing
base checkpoint: it reloads the selected monthly source parts and deterministically recomputes the
provenance, winner part, receipts, and checkpoint bytes before accepting them. Tier resume likewise
recomputes z9/z5/z0, all monthly markers, and the tier-checkpoint bytes from the verified z13 rows.

Writes are immutable and resumable. Every tier has exactly one Parquet part per month, with
`observed_day` retained in the rows. A base-month checkpoint lands after provenance and the monthly
z13 part; a tier-month checkpoint is SHA-bound to that exact base checkpoint and lands after the
z9/z5/z0 parts and all four monthly markers. Finalization
reconciles pinned source-part receipts to per-fact provenance, winners, all tier months, and the exact
object inventory. A lane `manifest.json` then lands, followed by `_COMPLETE` as the last write in
that lane root. The lane manifest carries the full sorted receipt set and a cryptographic inventory
root for every checkpoint, provenance part, tier part, and marker. The four-lane bundle binds each
lane manifest and completion receipt and uses the same manifest-then-`_COMPLETE` order under
`derived-canonical/signal-observation/_manifests/soil-temperature/`.

## Wind speed

`breakdown_wind_speed_snapshot.py` is pinned to the completed
`raw-canonical/signal-observation/snapshot=prod-20260826-full-signal-v1/` manifest and refuses any
other manifest hash. It never opens PostgreSQL or a publisher endpoint. Its bounded unit is one
canonical month×cell-batch ledger; every WS2M raw fact gets a provenance row, while serving winners
use newest `source_release.retrieved_at` then greatest observation id at the frozen signal grain.

The output lives under the non-live immutable prefix
`layer=climate-field-wind-speed/snapshot=prod-20260826-full-signal-v1/`. Source-unit checkpoints bind
the input ledger and parts to provenance plus staged winners. Day checkpoints bind independently
derived z13/z09/z05/z00 parts. A reconciled manifest closes the snapshot and `_COMPLETE` is written
last. Promotion into the live reader layout, API/client changes, and PostgreSQL retirement are
deliberately separate operations.

Resume validates each checkpoint against its current ledger/day input before reuse. A part orphaned
by a crash is adopted only when its schema, row count, and semantic row digest match, so a Parquet
library byte-encoding change cannot strand an otherwise identical run. Reopening a completed
snapshot re-reads every manifested part, validates every checkpoint binding and aggregate claim,
and checks the exact prefix inventory before reporting it healthy.

## Precipitation

`build_precipitation_from_canonical_snapshot.py` is the sole writer for the historical
`climate-field-precipitation` breakdown. It has no database surface: its only input is canonical
snapshot `prod-20260826-full-signal-v1`, and it refuses unless the source manifest bytes hash to
`465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f`. The legacy `layer=signal`
prefix is never an input or output.

The script follows the source manifest's month/cell-batch ledgers and holds one month at a time.
Every declared source part is checked by byte count, SHA-256, row count, and canonical row digest.
Rows are then classified into the legacy governed precipitation population or one mutually
exclusive exclusion reason. Within the governed grain, the winner is the newest source-release
retrieval timestamp and then highest physical observation ID. No physical row is discarded:
precedence-ordered lineage arrays retain every ID, canonical row hash, release ID, source-part
checksum, and row ordinal, while the selected fields repeat lineage element zero for direct audit.

Destination writes are conditional create-only operations. Existing bytes are accepted only when
identical; no key is overwritten, retracted, or pruned. A month checkpoint is written only after
all four day tiers and their completion markers read back correctly. The final manifest binds all
checkpoint hashes. A rerun verifies completed months and continues at the first absent checkpoint.

The canonical source manifest records each month ledger's identity, totals, and source-row digest,
but not the ledger object's byte checksum. The source-chain audit therefore proves every constructed
ledger has the exact manifest summary, enforces every selected part's exact snapshot-root path,
verifies its bytes and row digest, and records the ledger byte SHA-256 as consumed. Its immutable
`source-chain-audit.json` and `_AUDIT_COMPLETE` bind the output manifest SHA and land before the
destination `_COMPLETE`, which is the final immutable write. This audit runs on resume too, so a
completed destination never skips revalidation of its source receipts.

Coarse z09/z05/z00 rows derive directly from each z13 day. Row-level winner provenance becomes null
after aggregation because a coarse cell has no single physical source row; each day checkpoint
instead binds every derived object to the exact z13 part key and SHA-256 from which it was derived.
