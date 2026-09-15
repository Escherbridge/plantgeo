---
type: planning-evidence
recorded_on: 2026-09-14
author: /root/independent_verifier
status: prerequisites-open-no-database-created
---

# Isolated local lifecycle QA prerequisites

This is a read-only planning handoff for the authorized synthetic request, like, comment and
proposal lifecycle QA. No database command, installation, container start, application test or
data mutation was performed for this investigation. File inspection and read-only Podman
inventory were used. These prerequisites do not apply to the separate no-database fixture
browser suite.

## Database capability and container custody

The native PostgreSQL 16 extension directory inspected at
`C:/Program Files/PostgreSQL/16/share/extension` contains `pgcrypto.control` and
`btree_gist.control`, but no `postgis.control` or `vector.control`. Thus the supported empty
database bootstrap cannot yet complete there. This is package-file evidence, not a query of
installed extensions in an existing database.

Podman is installed at `C:/Program Files/RedHat/Podman/podman.exe`; Docker CLI was not found.
Initial sandboxed inventory could not access Podman's user configuration/socket. The subsequent
authorized elevated read-only inventory succeeded and reported the WSL Podman machine running.
No machine or container was started, stopped, created or altered by this lane.

The repository's [local warehouse Dockerfile](../../../../infra/local-warehouse/Dockerfile)
uses `postgis/postgis:16-3.4`, pinned to digest
`44126d872ac91993766c341e369c539e8196614321765d36a6f1bab0419a5fa5`. That upstream image is cached
as image ID prefix `06287eb8e12c`. The Dockerfile removes the vendor's automatic PostGIS init
script and deliberately installs no extension packages. Pgvector package availability in that
image remains unverified and must be established before relying on it.

The cached `localhost/plantgeo-spatiotemporal:pg16` tag points to image
`57b25f23c83fceacc4daae1b0104bc8fbed296ba1ba3795233ecd88823b74747`, whose metadata identifies a
legacy Timescale-derived image, rather than the current repository Dockerfile. A stopped
`agri-baseline-db` container refers to that image and loopback port 5442. Do not treat the tag as
proof of the current build or the stopped database as an empty QA fixture.

The next session should use a new unique container name, database identity, volume and unused
loopback port, after establishing a package-complete PostGIS/pgvector image. Preserve the
existing fixed `plantgeo_warehouse_pgdata` volume and other local applications. The project
[compose file](../../../../infra/local-warehouse/compose.yaml) is a topology reference, not an
instruction to reuse its fixed data volume for disposable QA. The
[warehouse instructions](../../../../infra/local-warehouse/AGENTS.md) require an explicit
extension gate before migrations.

## Supported schema order

Follow [deployment database order](../../../../docs/deployment.md) and
[bootstrap-database.mjs](../../../../scripts/bootstrap-database.mjs):

1. Establish a disposable target identity and local endpoint before writes. Install/enable
   `postgis`, `pgcrypto`, `vector` and `btree_gist` there.
2. From `services/agri-data-service`, run Alembic `upgrade head` against that target. The
   [Alembic environment](../../../../services/agri-data-service/alembic/env.py) reads
   `DATABASE_URL_SYNC`, not `DATABASE_URL`. Override the exact sync variable explicitly.
3. Run `node scripts/bootstrap-database.mjs` with an explicitly local `BOOTSTRAP_DATABASE_URL`.
   It requires `agri.spatial_cell` and `agri.data_source`, applies Drizzle migrations and loads
   `drizzle/seed` reference rows. Do not skip reference seeding: the interventions layer is
   required by submission and readiness expects eight named layers.
4. Pin runtime `DATABASE_URL`, migration variables and any role-operator DSN to the same
   disposable identity. Verify schema/ledger/reference rows before using the application.

The Alembic baseline owns retained control and lookup tables. This local social QA requires no
environmental observations, production dump or production object storage. Do not seed
intervention/request records or bypass the publication workflows.

## Synthetic identities and role gap

Use `/register` or `POST /api/auth/register` and normal credentials login. The
[registration route](../../../../src/app/api/auth/register/route.ts) creates contributor users,
hashes passwords and issues local verification-token rows. In development with no email provider,
the verification action URL is logged; redeem it through `/verify-email` when needed. Credentials
authentication does not itself require `emailVerified`, while some organization workflows do.

[promote-user.ts](../../../../scripts/promote-user.ts) is the supported operator path to expert
and admin roles: run it with the synthetic account email, the desired role and an explicitly
local `PROMOTE_DATABASE_URL`. It changes only `platform_role`. Sign out and back in afterward to
refresh the session. Do not create forged session cookies as a substitute for login.

The operator supports only contributor, expert and admin, matching the current gate vocabulary.
There is no supported platform-viewer provisioning path. A team viewer is a different
authorization dimension and cannot substitute for the QA plan's platform-viewer negative-write
fixture. Resolve that missing local fixture path explicitly in a bounded follow-up; do not
silently broaden the production role operator during setup.

## Local delivery and runtime isolation

Use a fresh loopback-only application with an isolated environment. Omit ambient application
credentials and override dotenv-defined values before Next loads them. Keep OAuth and provider
credentials empty, `ENABLE_LEGACY_BULLMQ_JOBS=false`, `SERVICE_ROLE=web`, and local
`NEXTAUTH_URL`/`NEXT_PUBLIC_APP_URL`. Use a synthetic local auth secret without recording its
value in evidence.

With `NODE_ENV=development` and `EMAIL_PROVIDER` empty,
[transactional-email.ts](../../../../src/lib/server/services/transactional-email.ts) logs local
verification/reset/invitation action URLs and [email.ts](../../../../src/lib/server/services/email.ts)
skips delivery. These single-use URLs remain credentials; retain their verification outcomes,
not their token values, in shareable evidence. Production mode intentionally does not offer
this local fallback.

Inspected request/proposal/social/moderation mutations contain no email/notification enqueue
operation. Their writes remain database-local; registration adds a local verification token.
No durable local email outbox was identified. Keep all background workers disabled. Development
registration can continue when Redis is unavailable, but `/api/ready` also probes Redis: full
readiness requires a separately isolated local Redis service and does not follow from DB-only
workflow success.

## Execution and acceptance scope

Use separate anonymous and synthetic contributor/expert/admin browser contexts. Exercise
`+ Request`, proposal drawing and consent through the real local UI. Check anonymous published
request reads, authenticated comment reads, contributor likes/comments, pending proposal
visibility, contributor moderation denial, and expert publish/reject through `/moderation`.
Relevant procedures are `interventions.submitRequest`, `submitIntervention`, `getFeature`,
`listProposed`, `interventionSocial` mutations/reads and `contributions.publishContribution` /
`rejectContribution`. Keep dormant request voting inactive.

Do not reuse the fixture suite's blanket mocked tRPC responses for these journeys: they would
hide the database and authorization behavior under test. Basemap fixtures may remain separately
identified, but authored social rows must pass through real consent and mutation paths.

The [QA specification](../spec.md) permits synthetic local mechanics evidence and forbids
production/training use of those fixtures. Such a pass cannot satisfy the governing real-human
contributor requirement, production acceptance, all-layer/source QA or scheduled burn-in. The
native extension gap, container image/pgvector verification and viewer fixture gap remain open.
