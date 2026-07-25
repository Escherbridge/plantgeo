---
type: code-styleguide
---

# PlantGeo JavaScript Standard

PlantGeo is TypeScript-first. New application logic belongs in `.ts` or
`.tsx`; plain JavaScript is reserved for tool configuration, narrowly scoped
runtime glue, migrations/import helpers that cannot use TypeScript, and vendor
interoperability. This document applies to every `.js`, `.mjs`, and `.cjs`
file in the repository.

## Scope and modules

- Do not add JavaScript when TypeScript can run in that context. Do not add a
  second implementation merely to bypass type checking.
- Use ESM for new files. Use CommonJS only when the invoked tool requires it,
  and isolate that compatibility boundary in one file with a short reason.
- Use named exports for reusable modules; default exports are permitted only
  where a framework or tool convention requires one. Do not create barrel
  files that eagerly execute imports or pull runtime-only dependencies into
  build configuration.
- File names are `kebab-case`. Keep a script's input, output, and operational
  contract obvious from its file name and `--help`/usage text.

## Safe language use

- Use `const` by default and `let` only for reassignment. `var`, `eval`, the
  `Function` constructor, prototype mutation, and dynamic module paths derived
  from input are forbidden.
- Use `===` and `!==`, braces for every control branch, trailing commas in
  multiline literals, and `async`/`await` rather than detached promise chains.
  Handle rejected promises at their boundary.
- Parse all CLI, environment, file, network, queue, and child-process data as
  untrusted. Validate shape, range, and size before use; do not rely on truthy
  coercion for booleans or numbers.
- Avoid shell strings and `shell: true`. When a child process is essential,
  pass an executable plus a fixed argument array, constrain paths to the
  intended workspace/temp directory, capture a bounded amount of output, and
  fail with actionable diagnostics.
- Never include credentials, Railway private hostnames, database URLs, exact
  sensitive locations, or raw AI prompts in output. Read secrets only from the
  documented environment variables and fail with the variable name—not its
  value—when required configuration is missing.

## Operational scripts and data jobs

- Scripts that modify data must be explicit about target environment, database,
  or file collection; default to dry-run and require an affirmative `--apply`
  (or documented equivalent) for mutations. Print counts and a request/job ID,
  not raw records.
- Make ingestion and backfill scripts idempotent. Require a stable source ID
  or deduplication key, bound batches and concurrency, use transactions where
  appropriate, and make retries safe.
- Treat downloaded geospatial data as hostile: cap file and feature counts,
  validate geometry and coordinate order, normalize to the domain schema, and
  retain source URL, license/attribution, observed time, and import version.
- External calls need timeouts, bounded retries with backoff and jitter, and
  `Retry-After` handling. Do not create infinite loops or unbounded
  `Promise.all` fan-out; give long work a cancellation path and progress logs.
- Railway instances are disposable and may run concurrently. Do not depend on
  local disk, process memory, or one instance owning a scheduled job for
  correctness. Persist coordination in the supported database/queue/cache and
  make job ownership explicit.

## Browser and worker glue

- A browser-only JavaScript module must be loaded from a client boundary. It
  cannot import server-only services or expose secrets through a public
  variable.
- Workers communicate through versioned, discriminated plain-data messages.
  Include request IDs and cancellation, validate messages on receipt, transfer
  large buffers where possible, and keep DOM, React, map, and credential access
  on the main thread/server respectively.
- Do not use JS to silently replace a TypeScript map layer. A layer must still
  observe the map data budget, viewport/zoom scope, cleanup, provenance, and
  accessibility requirements in the TypeScript and HTML/CSS standards.

## Quality gate

- Keep JavaScript small enough to review without inference. Add tests for data
  parsing, dry-run/apply branching, idempotency, and failure cleanup where a
  script changes data or deployment behavior.
- Run the command in its safe/no-op mode before documenting it. A script that
  deploys, migrates, imports, or deletes must document prerequisites, rollback
  or recovery, and its exact target.
