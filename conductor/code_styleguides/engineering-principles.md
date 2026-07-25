---
type: engineering-principles
---

# PlantGeo engineering principles

The cross-cutting standard every language guide inherits. It exists because
PlantGeo turns open environmental data into **real-world intervention plans**:
wrong-but-plausible output is worse than an honest gap. These four pillars are
enforced in review; the language guides (`python.md`, `sql.md`, `typescript.md`,
`javascript.md`, `html-css.md`) make them concrete per stack.

## 1. Reusability

- **One canonical definition per concept.** A schema object lives once in the
  declarative tree (`services/agri-data-service/db/agri/**`); a request/response
  shape lives once beside its service; a checksum/normalization rule lives in one
  function that every caller shares. Duplicated truth is a defect, not a
  convenience.
- **Depend on contracts, not implementations.** Callers take typed
  inputs/outputs (Pydantic model, SQL `RETURNS TABLE`, Zod schema, tRPC
  procedure) and never reach around them into internals. A new consumer must be
  addable without editing the producer.
- **Compose small, single-purpose units.** Prefer a function/module/view that
  does one thing and is reused over a bespoke copy. Extract on the *second* real
  use, not speculatively — reusability is proven by a second caller, not
  predicted.
- **No forking of governed logic.** Provenance, immutability, evaluation-only,
  and least-privilege rules are shared code paths. If two places enforce the same
  invariant differently, that is a bug even if both "work."

## 2. Clean code

- **Names carry intent; structure reveals it.** A reader should infer *what* and
  *why* from names and shape without a comment. Terse one-line doc-comments only;
  design rationale, module contracts, and operational notes go in the directory's
  `AGENTS.md` (see the repo-wide directory-doc convention), never a multi-paragraph
  source comment.
- **Small surfaces, explicit boundaries.** Keep functions short and cohesive.
  Make client/server, trusted/untrusted, main-thread/worker, and
  migration/runtime boundaries explicit and one-directional.
- **Fail closed and loud.** Required safety, permission, provenance, and
  publication checks raise on violation. Never degrade a write, recommendation,
  or intervention into a silent success. Never substitute a plausible default for
  a missing governed input.
- **No dead weight.** No commented-out code, unused branches, speculative
  abstractions, or `TODO` without an owner and tracking. Delete rather than
  disable.

## 3. Algorithmic excellence

- **Bound everything before you compute it.** Coordinates, zoom, date ranges,
  page sizes, feature counts, horizon steps, simulation counts, and result rows
  get an explicit budget at the boundary. No unbounded scan, download, or loop.
- **Push work to where the data lives.** Set-based SQL / PostGIS over row-by-row
  loops; server-side filtering and tiling over client-side; vectorized array math
  over Python loops on hot paths. Know the complexity of a hot path and its
  index/plan.
- **Determinism and numerical honesty.** Reproducible seeds, pinned UTC and
  format GUCs, and stable ordering for anything checksummed or evaluated. Prefer
  numerically stable formulations; state the method's assumptions (e.g. an
  exchangeable-increment bootstrap ignores seasonality) rather than implying more
  rigor than exists.
- **Leakage is an algorithmic bug.** A signal, feature, or forecast may only use
  information available at its as-of/issue time. Time-honest evaluation is
  non-negotiable for anything that becomes an intervention signal.

## 4. Enterprise quality: validation & testing

- **Validate at every ingress.** Every external value — API/model response, route
  input, env var, Redis/DB JSON, worker message — is untrusted until validated
  once at the boundary and given a named domain type.
- **Test behavior and its failure/partial paths.** Each behavior change ships
  focused tests covering success, failure/timeout/stale, authorization/tenant
  scope, boundary/leakage, and user-visible partial-data states — not internals.
  Contract/immutability invariants get their own tests (see the disposable-DB
  PostgreSQL contract tests and the schema-parity test).
- **Provenance travels with data.** Source, observed/published time, spatial
  resolution, cache/stale status, and known-missing inputs accompany every
  environmental value and every derived signal end to end.
- **Green gate, run once.** Before a change is ready, run the applicable
  formatter, linter, type-checker, and tests. In a multi-fix pass, apply all
  fixes first, then run the full sweep once. A wrong "done" costs more than an
  honest "unverified."

## Review checklist (applies on top of each language guide)

1. Is there exactly one canonical definition, consumed through a typed contract?
2. Do names and structure make intent obvious without prose comments?
3. Is every input validated at ingress and every computation bounded?
4. Is the path time-honest (no leakage) and deterministic where checksummed?
5. Does it fail closed on missing governed input, carrying provenance through?
6. Do tests cover the failure/partial path, and did the full sweep pass once?
