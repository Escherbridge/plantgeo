---
type: track-evidence
slug: gapless-parquet-product-ownership-census
status: in-progress
---

# P0 product and ownership census

## Verdict

P0 has frozen the physical product set, ownership handoff rule, terminal-state vocabulary and
availability artifact contract. It is **not complete**: this session was intentionally offline and
did not re-list production R2 or obtain fresh provider receipts. Source ceilings, several borrowed
lags/floors and incomplete ladders therefore remain operator-measurement blockers. No scheduler or
writer handoff is authorized by this evidence.

The current time-bearing inventory is **28 physical products**: nine mutable/event lanes (including
the transitional `signal` plane) and nineteen dedicated climate/soil products. The four
`static_lookup` streams (`calendar`, `evacuation-zones`, `soil-survey`, `watersheds`) are excluded;
they publish source-watermark versions rather than a slider day series. Every time-bearing product
uses the repository's canonical ordered required-rung identity `(0, 5, 9, 13)`.

## Mutable and event lanes

| physical lane | nature | code-declared floor / lag | current or legacy owner | proposed owner | unresolved source/ceiling evidence |
|---|---|---|---|---|---|
| `burn-severity` | release series | 2020-11-24 / 7d | legacy MTBS ingest cron | executor event family | fresh dated MTBS provider receipt and live ladder re-list |
| `drought` | release series | 2022-08-09 / 4d, weekly | historical USDM path; no proven active forward executor lane | executor event family | 2026-09-01 provider receipt and current published release |
| `fire-detections` | daily series | 2000-11-01 / 2d | legacy fire-forward path | executor event family after no-overlap handoff | source ceiling must be derived from FIRMS receipt, never browser future dates |
| `fire-perimeters` | daily series | 2025-07-28 / 1d | legacy current-perimeter poll | executor event family | live retained-history floor and provider settlement evidence |
| `sensors` | daily series | 2026-07-29 / 1d | legacy current-observation poll | executor event family | source cadence/history contract is not yet measured |
| `signal` | daily series, transitional | 2022-04-30 / 9d | immutable canonical snapshot; no single forward owner | split to dedicated source products | widening the NASA history floor and removing transitional ownership need explicit receipts |
| `vegetation` | daily series | 2022-08-05 / 7d | current vegetation forward/catch-up path | retained or executor handoff only after no-overlap proof | fresh provider ceiling and all-rung live re-list |
| `water-gauges` | daily series | 2026-05-24 / 2d | legacy water-forward path | executor event family | lag conflicts with same-day live evidence; writer ceiling is not frozen |
| `weather-observations` | daily series | 2026-08-01 / 2d fallback | legacy current-conditions ingest | executor event family | the floor/lag/cadence contract is explicitly a fallback and must be measured |

## Dedicated climate and soil products

These nineteen products are already distinct physical identities in schemas/snapshot outputs, even
where the current private snapshot allowlist has not yet caught up. Their present owner is the
immutable 2026-08-26 canonical snapshot build; none has a proven active forward executor lane.
P1/P2 own the new source-family writers. Provider, floor and source ceiling below remain bound to
the exact snapshot/bootstrap receipts rather than inferred from the displayed latest day.

| source-family handoff | products | 2026-09-01 observed tail evidence | proposed owner |
|---|---|---|---|
| climate | `climate-field-air-temperature-{min,mean,max}`, `climate-field-dew-point`, `climate-field-relative-humidity`, `climate-field-wind-speed`, `climate-field-precipitation` | latest selectable 2026-08-06; 27-day advertised tail | P1 bounded climate writer |
| solar | `climate-field-shortwave-radiation` | latest selectable 2026-05-31; 94-day advertised tail; lag/ceiling unresolved | P1 solar-first writer |
| NASA wetness | `soil-wetness-surface`, `soil-wetness-root-zone`, `soil-wetness-profile` | latest selectable 2026-08-06; 27-day advertised tail | P2 NASA POWER writer |
| ERA5-Land moisture/VPD | `soil-field-moisture-0-7cm`, `soil-field-moisture-7-28cm`, `soil-field-moisture-28-100cm`, `soil-field-vpd` | latest selectable 2026-08-02 for ERA5 family in browser evidence; exact per-product receipt pending | P2 ERA5-Land writer |
| ERA5-Land temperature | `soil-temperature-0-to-7cm`, `soil-temperature-7-to-28cm`, `soil-temperature-28-to-100cm`, `soil-temperature-100-to-255cm` | latest selectable 2026-08-02; 31-day advertised tail | P2 ERA5-Land writer |

## Frozen publication rules

- A rung is terminal only as `published` with one or more immutable data-part receipts plus its
  completion receipt, or `governed_absence` with a source receipt and non-empty reason.
- A day may enter an availability generation only when all `(0, 5, 9, 13)` rows exist and agree on
  terminal state, source receipt and absence reason. Partial ladders remain nonterminal work.
- Source ceiling is receipt-derived. A browser date, local clock, job attempt, empty query or prior
  catalogue ceiling is not source-settlement evidence.
- Existing and replacement owners may never overlap. P5 remains separately operator-authorized.
- Bootstrap is a one-time exact read of checksum-pinned manifests/checkpoints. Normal publication
  extends the winning generation and never re-lists history.

## Open blockers before P0 can complete

1. Capture 2026-09-01-or-later provider receipts and a fresh production R2 ladder census; this
   offline implementation session did neither.
2. Freeze the shortwave source lag/ceiling rather than inheriting the stale 94-day tail.
3. Resolve water's lag conflict and declare the old/new writer ceiling.
4. Replace the weather-observations fallback floor/lag and measure sensor cadence/history.
5. Decide and receipt the NASA floor widening before authoring owed historical days.
6. Bind every lane's initial availability input to exact manifest/checkpoint keys and SHAs; no
   bootstrap has been executed in production.

## Sources read

- `conductor/RUNBOOK.md`, LIVE publication and production-timeline sections (2026-09-01).
- `pipeline/parquet/lane_registry.py` current nature/floor/lag registrations.
- `parquet_ops/snapshot_products.py` current private snapshot identities.
- `docs/reports/data-lane-execution-ownership-2026-08-28.md`, used only as historical ownership
  context; it predates the registered dew-point and three wetness products and is not a current
  source-ceiling receipt.
