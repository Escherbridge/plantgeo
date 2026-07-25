---
type: code-styleguide
---

# PlantGeo TypeScript, React, and Service Standard

This is the required standard for `src/**/*.ts` and `src/**/*.tsx`. It is
specific to PlantGeo's App Router, MapLibre/deck.gl/Three.js map, PostGIS
services, AI-assisted planning, and Railway deployment. It supplements the
compiler's strict mode; it does not replace it.

## Baseline

- Write strict, portable TypeScript. `any`, `@ts-ignore`, `@ts-nocheck`, and
  broad `Record<string, any>` are forbidden. Receive untyped values as
  `unknown`, validate them, then expose a named domain type.
- Prefer `const`, `readonly`, discriminated unions, and exhaustive `switch`
  handling. Use `satisfies` for configuration whose shape should be checked.
- Use named exports for reusable application code. Default exports are reserved
  for Next.js entrypoints and a library API that explicitly requires one.
- Name React components and types in `PascalCase`, functions, hooks, variables,
  and object properties in `camelCase`, and constants in `UPPER_SNAKE_CASE` only
  when their values are true process-wide constants. New feature component files
  use `PascalCase.tsx`; low-level primitives follow the established
  `components/ui` kebab-case convention. Hooks use `useThing.ts`; other modules
  use `kebab-case.ts`. Do not rename unrelated files just to apply this rule.
- Use `import type` for types. Do not introduce barrel exports that pull browser
  libraries, server-only code, or optional map layers into an unrelated bundle.
- Avoid non-null assertions and broad casts. A narrow interop cast is permitted
  only at an external-library boundary, must be localized, and needs a terse
  reason beside it. Do not cast to hide an invalid model.
- Keep comments short and local. Put design rationale, contracts that span
  modules, and operational notes in the closest `AGENTS.md`, not a long source
  comment.

## Boundaries and validation

- Treat route params, `request.json()`, URL search parameters, environment
  values, Redis payloads, database JSON, worker messages, `postMessage`, and
  every external API or model response as untrusted input. Validate once at the
  ingress with a Zod schema or a dedicated type guard; do not let raw JSON flow
  into a component, store, or database query.
- Put shared request/response schemas next to their domain service, not in a
  UI component. Infer the exported type from the schema where practical so the
  validator and type cannot drift.
- Route handlers and tRPC procedures must validate input, authenticate when the
  operation is not genuinely public, authorize the specific resource/tenant,
  apply an appropriate rate or cost limit, and return a stable, documented
  error shape. Client-side gating is never authorization.
- Keep `src/lib/server/**` server-only. Never import a server module, a secret,
  a database client, or an internal Railway hostname into a client component or
  `NEXT_PUBLIC_*` variable. Public map source URLs must be intentionally public.
- Parameterize Drizzle/PostGIS queries. Bound coordinates, zoom, date ranges,
  page sizes, feature counts, and geometry complexity before querying or
  transforming data. Query PostGIS for spatial work; do not download a large
  dataset to the browser to filter it.

## Next.js and React

- Server Components are the default. Add `'use client'` only to the smallest
  interactive leaf that needs browser APIs, effects, a store subscription, or
  a map library. Keep pages and layouts server-rendered where possible.
- Browser-only MapLibre, deck.gl, Three.js, WebSocket, and worker creation must
  be behind a client boundary and dynamically loaded when their bundle or SSR
  incompatibility warrants it. Clean up map controls, event listeners, streams,
  animation frames, workers, and WebGL resources in effect cleanup.
- Render must be pure: no timers, requests, store writes, random IDs, or state
  transitions during render. Start asynchronous work in an effect, event
  handler, action, or query hook; make it cancellable with `AbortController`.
- Components receive typed props, own one understandable responsibility, and
  expose semantic states for loading, empty, partial, stale, error, and success.
  Split a panel when its data orchestration and visual sections stop being easy
  to test independently.
- Subscribe to the narrowest Zustand/Jotai state slice. Do not read an entire
  store in a high-frequency map component, and do not mirror server data in
  multiple stores without a single source of truth and invalidation plan.
- Use stable domain identifiers as React keys. Array indexes are allowed only
  for a permanently static, non-reordered list.

## Map, deck.gl, and worker performance

- The render thread owns DOM, React state, MapLibre, deck.gl, and Three.js.
  Workers own CPU-heavy, deterministic work: GeoJSON parsing and validation,
  simplification, clustering, spatial indexing, scoring, aggregation, and
  serialization for the action network. A worker must not access DOM, map,
  React, Zustand, secrets, database clients, or `window`-only services.
- Define versioned, discriminated request and response message schemas for each
  worker. Validate both directions, include a request ID, support cancellation,
  discard superseded responses, and transfer `ArrayBuffer`s instead of cloning
  large typed arrays. Keep worker results as immutable plain data; layer
  creation remains on the main thread.
- Prefer vector tiles, server-side filtering, viewport/zoom-aware queries, and
  deck.gl layer updates over large client-side GeoJSON. Declare a feature and
  response-size budget before adding a layer. Paginate or tile any unbounded
  collection.
- Memoize derived layer inputs and update only the affected deck.gl layer.
  Avoid recreating a map, overlay, source, layer, event handler, or style object
  on every React render. Throttle pointer and camera events and use
  `requestAnimationFrame` for visual updates.
- Preserve coordinate order explicitly (`[longitude, latitude]` for GeoJSON and
  MapLibre). Name values `lat` and `lon`; do not pass anonymous numeric tuples
  across service, worker, and UI boundaries without a named type or schema.
- Treat WebGL context loss and a worker failure as recoverable UI states: stop
  work, release resources, explain the state, and offer a safe retry. Never
  spin an unbounded retry loop.

## External data and service reliability

- Fetch third-party environmental data in a server-side service, not directly
  from a display component. Centralize the source URL, attribution, schema,
  timeout, cache policy, and normalization with the service.
- Every outbound request has a timeout, bounded response/body size where the
  client permits it, status check, schema validation, and contextual error.
  Retry only idempotent operations with capped exponential backoff and jitter;
  respect `Retry-After` and never retry invalid requests or permanent 4xx
  responses.
- Cache deliberately: name the key by data version and spatial/time scope, set
  a TTL, state whether stale data may be served, and invalidate after mutations.
  An in-memory cache is an optimization only and must not be the source of
  correctness on Railway's multiple or restarted instances.
- Carry provenance with environmental values: source, observed/published time,
  spatial resolution, cache/stale status, and known missing inputs. Partial
  results must remain partial—do not replace an unavailable source with a
  plausible-looking default.
- Use `Promise.allSettled` when independent sources may fail, then expose which
  sources failed. Fail closed for required safety, permission, and action
  checks; do not silently degrade a write or recommendation into an unsafe
  success.

## AI and action-network controls

- AI output is decision support, never a command to deploy an intervention,
  modify a layer, contact a partner, or spend resources. Keep tool permissions
  allowlisted and server-side; a model cannot choose a broader capability.
- Separate system policy, trusted normalized environmental context, and
  user-provided text. Delimit and label untrusted text, limit conversation and
  token size, retain only the history required for the request, and validate
  structured model output before it reaches the UI or any downstream action.
- Show the evidence, sources, data freshness, uncertainty, and a clear
  “recommendation, not a guarantee” status with each AI recommendation. Do not
  imply that a score is a field assessment or regulatory approval.
- Any consequential action requires an explicit user review screen, scope and
  impact summary, confirmation immediately before execution, authorization,
  idempotency protection, and an auditable record of actor, inputs, evidence,
  result, and time. Provide cancel and retry behavior that does not duplicate
  the action.
- Redact credentials, exact sensitive locations, personal data, and raw model
  prompts from logs. Log request IDs, source health, and safe diagnostic detail
  instead. Rate-limit expensive AI and ingestion endpoints per authenticated
  actor and relevant abuse boundary; define and test the availability posture
  rather than defaulting to fail-open.

## Tests and review gates

- Add or update focused Vitest coverage with each behavior change. Test parsing
  at ingress, authorization and tenant scope, service failure/timeout/stale
  paths, worker protocol/cancellation, coordinate order, and user-visible
  loading, error, empty, and partial-data states—not implementation details.
- Mock external services at the network/service boundary. Tests must not call
  live environmental APIs, AI providers, Railway, Redis, or PostGIS unless they
  are explicitly labelled integration tests with controlled fixtures.
- Before a change is ready, run the applicable formatter/linter, `npm run
  type-check`, and tests. A map or worker change also needs a manual check for
  cleanup, a degraded-data scenario, keyboard access to its controls, and a
  reasonable viewport-size data budget.

## Review checklist

1. Are client/server, trusted/untrusted, and main-thread/worker boundaries
   explicit and enforced?
2. Is the spatial query and payload bounded, cancellable, and attributable?
3. Can a user distinguish live, cached, stale, missing, model-derived, and
   confirmed information?
4. Does every state-changing operation verify permission and require the right
   human confirmation?
5. Do tests cover the successful path and the failure/partial path?
