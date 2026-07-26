---
type: conductor-index
---

# PlantGeo Conductor

Conductor is the maintained decision and work registry for PlantGeo. It is not
an alternative source of runtime truth: code, migrations, tests, and governed
receipts remain authoritative for what has actually happened.

## Authority order

1. Governed database state, receipts, and checked-in implementation establish
   executed facts.
2. `docs/` establishes runtime, data-custody, and release-operation contracts.
3. [`release-governance.md`](./release-governance.md) establishes whether a
   production release is permitted.
4. [`tracks.md`](./tracks.md) is the sole current work registry. A listed
   track's `metadata.json`, specification, and plan define its scoped work.
5. The numbered material under `tracks/` is retained product history/backlog;
   it is never an execution or release authority unless promoted into
   `tracks.md`.

## Status vocabulary

`active` is currently executing; `planned` has a defined next gate; `blocked`
cannot proceed without named evidence or authorization; `complete` has met its
defined deliverable; `historical` is retained context only. A status is updated
in the registry and the track metadata together.

## Operating rule

Start from the registry, then the governing track. Preserve historical evidence
and link to it rather than rewriting it as present state. A track may not
authorize a Railway mutation, forecast publication, strategy efficacy claim, or
model promotion unless the release policy says its exact gates are met.
