---
type: evidence-packet
track: parquet_production_acceptance_20260901
recorded_on: 2026-09-12
status: reconciled_locally_release_blocked
source_commit: 843b4b313e03447594b23a67f75c3062b2b1a024
source_tree: 9533bb9e5423240630935df0cd012cd8ead15504
source_task: 01a0947d-9cac-78a3-93ee-2ca18b7b05cd
parent_task: 01a09475-01c4-7252-8710-a8a57559c919
---

# Operational and release packet reconciliation

**Local reconciliation is complete; the operational release remains blocked.**
This packet assembles retained repository evidence for the application, Parquet
API, executor, product publications and rollback handoff. It does not satisfy
the current QA matrix's **FD** identity requirement or close production
acceptance A0–A4. Every unresolved binding below remains blocked.

Only committed repository files and local Git objects were read. No Railway,
production database, local database, `pgt`, object storage, writer, scheduler,
deployment state or remote Git endpoint was accessed. Object keys, deployment
IDs and URLs below are retained references, not resources contacted by this
task. Future evidence requests are owner handoffs, not authorization or commands
executed by this reconciliation.

## Packet and authority

- [pins.json](pins.json) holds exact local identities, dated service observations,
  retained temperature and MTBS bindings, unproved runtime slots and blockers.
- [declared-executor.json](declared-executor.json) inventories checked-in job and
  product declarations. Declared, historically enabled and effectively running
  are separate states.
- [source-manifest.json](source-manifest.json) binds consulted sources to their
  base-commit Git blob IDs and SHA-256 of committed bytes. These are local source
  hashes, not re-hashes of remote objects mentioned inside those sources.
- [validation.md](validation.md) records local packet validation and separate review.

The [Conductor authority order](../../../../README.md),
[release governance](../../../../release-governance.md),
[current runbook](../../../../RUNBOOK.md),
[work registry](../../../../tracks.md),
[deployment contract](../../../../../docs/deployment.md), and this track's
[specification](../../spec.md) and [plan](../../plan.md) govern interpretation.
No plan checkbox, shared ledger entry, track status or release authorization is
changed. The packet owner is this local reconciliation task; operational
ownership stays with the tracks named in the blocker register. No individual
runtime operator is designated by this packet.

## Frozen source and service identity

At intake, local `main` and HEAD both resolved to
`843b4b313e03447594b23a67f75c3062b2b1a024`, tree
`9533bb9e5423240630935df0cd012cd8ead15504`; the worktree was clean and detached.
The local branch is `codex/operational-release-packet-20260912`. This is the
locally available main state, with no fetch. The evidence commit/tree is reported
after commit to avoid a circular self-hash.

| Source scope | Exact local tree | Meaning |
| --- | --- | --- |
| Whole application/repository candidate | `9533bb9e5423240630935df0cd012cd8ead15504` | Includes application source, manifests, build and migration definitions; not a built image. |
| Application `src/` | `1aa29ff3abd72ec825c970d8d78f96bff3437618` | Source subtree only; not the whole application build context. |
| `services/agri-data-service/` | `93647d7a0403acdb9623343583119df756a53ab7` | Python source, dependency lock, configurations and retained quality receipt at the same commit. |
| `infra/job-executor/` | `f8bd198b41b0a7fed5941728e9cc047e2a5c436f` | Executor build definition subtree; its image also copies Python and application inputs. |

The remaining QA matrix froze F0 at `64f4f892bd2b744cc097c7f76a1f239997b80f52`,
tree `f8697da694a3f9fbbf28e70ff7581bd59546bfd6`. Its diff to this intake contains
only Conductor documentation; application, Python service and executor source
are unchanged. This reconciles local source custody only. Existing captures
retain their original revisions and scope, and no runtime result transfers to
this candidate merely because a source subtree matches.

A service identity must bind project, environment, immutable service ID,
deployment ID, full commit/tree, build context and Dockerfile, image/build
identity, effective command/configuration, release metadata and observation
time. The retained project is `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`; the retained
production environment ID is `b7cfa813-8a5c-4fcd-80f2-cab736d840a7`. Those dated
identifiers do not establish current membership or state.

| Service | Retained service ID | Strongest later retained observation | Current binding |
| --- | --- | --- | --- |
| `plantgeo-main` | `fa08a3aa-6d1d-43eb-846b-15dbfd887d61` | September 10 deployment `c9719f3a-d159-4205-9567-a1e94009d08e`; September 11 MTBS narrative says `fa202230958fb55521963e886eb031be5fc266c4` deployed successfully, readiness at 05:56:45 UTC. | **B01/B02:** latest deployment ID, full configuration and source/image binding absent. Do not join September 10's deployment ID to September 11's commit. |
| `plantgeo-parquet-api` | `33aed861-af76-4fdd-a95e-784bdcc95e55` | September 10 deployment `4a0f8ccb-437f-4b28-9673-42534e11cd97`; September 11 narrative records the same MTBS correction deployed. | **B01/B02:** latest deployment ID and effective private-reader profile/configuration unbound. |
| `plantgeo-job-executor` | `565ecaad-9946-48f1-8a0b-28fa60494a16` | September 10 deployment `7e851cf5-6174-43cd-8262-1d14bfbe7ac1`; September 11 narrative records the same correction deployed and one reconciled burn definition resumed. | **B01–B03:** latest deployment ID, current active/required settings, persisted definitions and quiescence unbound. |

The September 2 census and September 3 mixed-version report remain historical
inputs. Executor deployment `b1f35a20-6e05-48ff-9801-5235c9753a01` is explicitly
bound to `e4490c3c2f2e23f75cc9d6e297f4be646e0e00a1`; it was reached by manual
redeploy after a failed push build. The September 3 report records a new web
revision with old Python services. This is direct retained evidence that one
Git SHA or successful application deployment cannot identify all three services.

## Declared build/configuration versus effective configuration

The application Dockerfile declares `node server.js`, port 3000, public build
arguments and its integrated frontend build gates. The deployment guide records
service-side pre-deploy `node scripts/migrate.mjs`, readiness `/api/ready` with
60-second timeout and restart `ON_FAILURE`/5 since the root `railway.json` was
deleted in `fd79875`. Current source pins include the migration contract and
build inputs; applied migration state and actual build arguments remain B02.

The actual checked-in API configuration is
`services/agri-data-service/railway.json`, with Dockerfile `Dockerfile`, readiness
`/ready`/30 seconds and restart `ON_FAILURE`. Its Dockerfile starts the Sanic
factory with `${PORT:-8000}`; the dated service inventory describes private port
8080. Effective root directory, port override, profile, blueprint/route set,
endpoint references and authentication configuration must therefore be bound by
B02, not inferred from either default.

The actual executor configuration is
`services/agri-data-service/railway.job-executor.json`, declaring
`infra/job-executor/Dockerfile`, `agri-service ops jobs-executor`, restart
`ON_FAILURE`/10 and no Railway cron schedule. The two Python Dockerfiles gate
their source against `QUALITY_RECEIPT.json`; the executor also copies Node and
application inputs for the SoilGrids driver. Neither configuration file proves
that a running service selected it. Python's base image is digest-pinned in
source; this does not identify the final built image.

Repository-root `railway.json` is absent and has a retained deletion commit;
the actual Python configuration paths above remain tracked. Short filename
references must be resolved against those paths and the effective service root.
The deployment guide also retains a `Plantgeo`
database reference that its own dated census says cannot resolve. The packet
records this inconsistency and leaves the effective database target blocked;
it does not repair configuration or test a connection.

The retained Python quality receipt was generated September 11 at
05:50:04.680279Z, domain `plantgeo.agri-data-service.quality-receipt.v2`, digest
`c9739cb76c667db0820727b2f634beee725b98938c78537f8f78ec86469c53c1` over 1,307
inputs. Its scope is historical, not an assertion that the intake tree passes.
The local binding check is recorded in validation; no receipt is regenerated.
The September 12 root QA receipt reports missing frontend tooling and a partial
Python suite with 510 errors. Those gates remain B09.

## Executor ownership and product coverage

`plantgeo-job-executor` is the designated sole scheduler and durable invocation
owner. The September 2 directive and handoff require exact release/deployment,
registry/lease/recovery proof, fresh no-overlap evidence and the specified
operator handoff. Historical 38 registered / 37 executable responsibilities
are dated census counts, not the current static registry or current active set.

The September 10 cutoff checkpoint changed `ACTIVE_LANES` from a recorded
28-lane list to a reviewed 20-lane list at `2026-09-10T13:48:23.7340624Z`, using
`skipDeploys: true`, `staged: false`. Its status is explicitly
`configured_pending_deployment`. The eight pauses were not shown effective in
that receipt. Later code deployment and the September 11 burn-lane resume do
not supply the complete active/required list, persisted definition versions,
processes, external invocations, leases or attempts before and after cutoff.
The September 12 cutoff reconciliation confirms this gap.

For MTBS, the retained narrative reports that normal registration preserved an
older persisted v2 definition. It records explicit reconciliation to daily
08:55 UTC, 2,130-second worker budget, 2,220-second lease and seven-day source
capture, then restoration of the original enabled state with no active work.
The raw definition/resume/readback JSON is absent. This is bounded historical
evidence, not a present all-lane ownership or scheduled-burn-in receipt.

The static inventory contains 32 Parquet registrations and 59 executor
responsibilities: 58 executable declarations and one snapshot-only terminal
responsibility. It accounts for source registry entries regardless of runtime
activation. Each product's current
immutable release, availability and ownership slots remain blocked unless
separately supplied; registry presence does not demonstrate publication.
Gapless publication owns P3/P4 runtime ownership and scheduled/recovery proof;
environmental retirement owns the effective cutoff and remaining fixed-support
dependency. Source-direct climate, soil and vegetation still read fixed support
from `agri.spatial_cell`; no admitted bucket-pinned replacement support artifact
is supplied by the retained cutoff packet.

## Immutable products, availability and artifact custody

### Temperature historical generations

The [retained machine receipt](../../../environmental_postgres_retirement_20260904/evidence/temperature-bootstrap-receipts-20260910.json)
contains exact mean/min/max generation keys, SHA-256, semantic generation
receipt hashes, bootstrap receipt keys/SHA, source inventory roots and byte
counts. `pins.json` preserves all field values and the associated operator-input
digests from the [runtime repair receipt](../../../environmental_postgres_retirement_20260904/evidence/parquet-runtime-repair-20260910.md).

| Product statistic | Recorded generation SHA-256 | Bytes |
| --- | --- | ---: |
| Mean | `527989e565b95d6369a59fa8e0b6404b58969fddc1f919d7e6a01f90c538f3b2` | 793655 |
| Minimum | `a607e073e88702bcdd4a7a0278ff3aabf147ef0d281acd2b283ee554823c1377` | 794128 |
| Maximum | `962763ebdc1325cf32949e43c8c581f77869f6a926ae2d32d012c1810a80757d` | 793493 |

Each records 6,240 rows, 1,560 complete days at each rung `[0,5,9,13]`,
2022-04-30 through 2026-08-06, source ceiling 2026-09-05, schema version `1`.
The later source ceiling is not a claim that the forward interval is filled.
The report's final publication section supersedes its earlier pending checkpoint.

The receipt says historical pointers were independently checked. Raw `_LATEST.json`
bytes/SHA/ETag, bootstrap marker body/SHA, immutable generation and bootstrap
objects, per-day terminal/source/part/completion inventories and current readback
are not retained here. Thus the exact historical generation pins are usable
evidence references, while current pointer/generation/bootstrap binding remains
B04/B05. Recorded `prior_generation_*: null` does not identify a rollback target.

### MTBS current replacement snapshot

The [September 11 rollout report](../../../environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md)
records source capture September 10, next-UTC-day availability September 11,
PNW bounds `[-125,42,-111,49]`, 747 unique fires in replacement years 2018–2026,
and explicitly incomplete 2023–2026 seasons. It records manifest SHA-256
`4690af4629e617ffbde3f5e6ae99be3eb4c1a536fe47a931d7f0a8456c8f6468` and exact
sorted fire-ID set SHA-256
`875ead71e552c1808182f25e7c8d6c1f05621d9cb518877b5c5c895a61a4f3a7`.

The publication commit `3632d616dc43f3845dbcb901e625b946b82519ba` resolves locally
to tree `61e00986fbffe9cda13d45fdd20a3e4a35444fa4`; reader correction
`fa202230958fb55521963e886eb031be5fc266c4` resolves to
`b356db4849b488f09a2cbdf5476c6e143cc60980`. Publication at 05:38:23 UTC and
four-rung verification at 05:44:21 UTC are dated report claims. The tested
540-row historical response retained its pre-existing truncation flag; it
does not satisfy all-product `truncated=false` or historical completeness.
Pre-2018 recovery and three later scheduled advances remain open.

The report's frontmatter `source_sha256` identifies the original narrative,
not the publication manifest or current retained file. It and the current Git
blob/content hashes are separately recorded. The referenced manifest body,
publication audit, deployment matrix, prepared ID proof, definition readbacks,
resume and browser artifacts are absent from the committed checkout (B06).
Immutable object/release key, release descriptor bytes, availability generation,
pointer and rollback identity remain null; none can be derived from a manifest
digest or code commit alone.

### Other products and preserved repair candidates

All other declared product entries inherit B04/B05 until exact release and
availability evidence is supplied. Static/snapshot products require their
contract-specific immutable release and requested/served-day binding; they are
not assigned a fabricated daily axis. Soil-survey's unadmitted Parquet reader,
static SoilGrids restoration, sensor/signal corrections and future forecast and
botanical releases retain their distinct owner gates (B10).

The September 12 admission receipt retains signal archive SHA
`4ca8a36083474d55426d8032275306198af5e30349c22407bc7f13bb6f768124`, sensor archive
SHA `eb3ca823a0a3319cc42e47c4066893cfd88efd58979bfb1e177949d9eb70537e`, and old
static-soil manifest SHA `a70359386bea7468b10bda0665f3b1728dd9c0c5118c8d94300e4b7933e92c5f`.
Their underlying artifacts are absent. These identify prepared or historical
inputs, not admitted immutable product releases. Sensor source completeness
and remote full-object soil identity were explicitly unproved.

## Blocker register and intake requirements

Every null current slot in `pins.json` points to one or more IDs below. Named
tracks are established accountable lanes; the authorized operator's identity
is **unassigned** in this packet and must accompany any operational handoff.
No blocked field is treated as zero, empty, disabled, inapplicable or healthy.

| ID | Missing field/artifact | Accountable owner | Next action / closure evidence |
| --- | --- | --- | --- |
| B01 | Current application/API/executor deployment IDs, full commit/tree, image/build/release identity, project/environment/service binding and capture time | Production acceptance A0/A4; authorized service operator unassigned | Supply one timestamped retained service matrix with per-service build/revision evidence, reconcile to the frozen candidate and retain raw response hashes. Do not combine observations across dates. |
| B02 | Effective roots, Dockerfiles/config paths, commands, watch/build inputs, public build values, private endpoint/DB references, profile/blueprints, auth policy, coverage/cache settings, readiness/restart/migration results and release metadata | Service operator unassigned; production acceptance A0/A4 | Supply a redacted effective configuration/release export for each exact deployment, with immutable identity for protected configuration and no secret values. Compare it with the hashed declarations. |
| B03 | Effective active/required lanes, persisted definitions/version/fingerprint, full ownership map, before/after processes, leases, attempts, external invocations and cutoff | Gapless P3; environmental retirement | Supply the complete timestamped definition/configuration/ledger readback bound to B01/B02, assign the operator and prove no overlap and effective cutoff. An accepted variable update is insufficient. |
| B04 | Current per-product `_LATEST` body/SHA and concurrency token, generation/receipt, bootstrap marker/receipt, schema/rungs and authoritative source inventory | Gapless publication; reader R0; production acceptance A1 | Supply a captured per-product binding chain and hashes on the same observation boundary. Label static/census exceptions explicitly and preserve current versus historical generations. |
| B05 | Exact immutable release/manifest key and bytes, source roots, source/terminal/absence receipts, physical parts/completion hashes, bounds/counts/truncation and rung inventory for every admitted product/day | Product publication owners in gapless; reader/renderer owners; production acceptance A1/A2 | Supply content-addressed manifest and verification inventories matched to B04 and the product/day/zoom matrix. A Git SHA, object listing or manifest digest alone is insufficient. |
| B06 | Decisive ignored MTBS, temperature verification, cutoff and prepared-repair artifacts referenced by retained summaries | Originating publication/retirement owners; production acceptance custody intake | Restore the named artifacts into a retained content-addressed evidence bundle and re-hash against recorded pins; if unavailable, state that and obtain new authorized evidence. Never fabricate omitted bodies from summaries. |
| B07 | Exact rollback service revisions/images/configuration and prior immutable product/pointer identities, compatibility and observability/restore proof | Production acceptance A4; service operator and gapless owner unassigned | Supply reviewed compatible rollback targets and receipts bound to the same matrix. Preserve manifests/checkpoints; executor rollback disables the affected lane and never recreates cron/old writers. No target is selected here. |
| B08 | Three consecutive scheduled advances per activated product; retry/restart/expired-lease recovery and no-overlap results | Gapless P4; production acceptance A3 | Supply run/time/owner/input/output/receipt/checkpoint identities for every interval and recovery exercise. Historical construction and configuration readback cannot count as future scheduled success. |
| B09 | Exact deployed-tree integrated quality, route, cold/warm, source-ceiling, selected/served/painted-day, cache, spatial/browser/mobile and independent acceptance evidence | Production acceptance A1/A2/A4; platform QA; reader and multiscale owners | Complete owner packets and one final integrated release sweep on the reconciled deployed tree. Retain skipped/failed gates; local packet validation is not a release-quality pass. |
| B10 | Admission and immutable support/release identities for signal/sensors, static soil/soil-survey, fixed spatial support, forecast and botanical planes | Environmental retirement; relevant forecast, Herbaria and botanical tracks in tracks.md | Supply each separate contract's admitted artifact, rights/lineage/review and supported reader evidence. Preserve blocked scope and do not promote prepared rescue inputs or transitional authoring data. |

Release governance still requires all applicable certification gates, exact
revision, reviewed rollback/observability plan and separate operator authority.
The completed September 9 rebuild, temperature history and bounded MTBS rollout
stay completed within their retained scope. This reconciliation authorizes no
new retirement, publication, scheduling, migration or production mutation.
