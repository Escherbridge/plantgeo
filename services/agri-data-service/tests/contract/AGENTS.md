---
type: reference
---

# The `/api/v1/parquet` wire freeze

## Why this directory exists

RUNBOOK §0.42.2. Three lanes build against this contract concurrently — lane B writes the routes
(pivot slice `d3`), lane C writes the readers (`d4`, `u1`–`u3`). Without a shared artefact both
lanes guess, and every guess that misses is rework discovered at the join. **This is step zero of
the programme: the only thing that must finish before anything runs in parallel.**

The contract itself was already written down — the `WIRE` block and the zod schemas in
`src/lib/server/services/parquet-plane-client.ts`. What was missing was a form the *serving* side
could assert against, since it is Python and cannot import a zod schema. So the freeze is one set of
golden JSON payloads plus two independent assertions over them.

## What is frozen, and what deliberately is not

| frozen | not frozen |
|---|---|
| route segments, query parameter names, the base path | the row *contents* of a published day |
| the four states and every field on each | how many rows a day holds |
| snake_case on the wire, camelCase in TypeScript | the order of lanes in a coverage census |
| `null` bounds for a lane that has never been written | |

Row contents stay open on purpose: the warehouse has a schema per layer per kind
(`warehouse/parquet/schema.py`), so one row shape here would be a lie about eleven of the twelve
streams. `rows` is `list[dict]` and the caller that knows its layer narrows it.

## The three properties worth more than the shapes

1. **A carried-forward release is reported at its own day.** `served_day` is the release's date, not
   the date asked for. Getting this wrong dresses a week-old USDM release up as today's — the same
   rule `PublishedDroughtCollection.carryForwardDays` already enforces. Two fixtures exercise it.
2. **A window answers every day in its closed range, ascending, with gaps stated.** A short array
   reads as "the missing days are fine". `decodeWindow` enforces it client-side; the contract test
   enforces it on the fixture so the server cannot ship a short array in the first place.
3. **Days never carry a timezone.** They are ISO string prefixes and nothing converts them. A `T` or
   a `Z` in a `*_day` field is how 6,279 of 16,743 water-gauge rows once moved to the wrong day, so
   the test walks every fixture and refuses anything that is not ten characters of `YYYY-MM-DD`.

## How the freeze actually bites

- `test_the_typescript_client_still_agrees_with_this_contract` **parses the `WIRE` block out of the
  TypeScript source** and compares it to `wire_contract.py`. Rename a route on either side and this
  fails. That cross-language check is what makes it a freeze rather than two hopeful copies.
- `_Frozen` sets `extra="forbid"`: a field nobody announced is a contract break, not a courtesy.
  Note the asymmetry — zod strips unknown keys by default, so **the Python side is the strict one**.
  A server adding a field fails here and passes silently in TypeScript.
- `src/__tests__/services/parquet-plane-client.test.ts` reads the **same** fixture files through the
  real zod schemas and the real mapping. Both sides consume identical bytes.

## Changing the contract

Edit the `WIRE` block, `wire_contract.py`, and the fixtures **in one change**, and expect both test
suites to fail until all three agree. That is the point. A lane that needs a new field asks for the
contract change first; it does not add the field and discover the mismatch at the join.
