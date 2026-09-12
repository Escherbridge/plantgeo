---
type: acceptance-handoff
date: 2026-09-11
status: active
---

# Wind & Weather audit handoff

The parent audit direction received during integration treats the current
Wind & Weather toggle as **sampled weather estimates/observations, with forecast
horizon 0**. This batch does not create a forecast product. The audit's recorded
history gap is **2021-11-27 through 2026-07-31**, including **2025-04-28**.
These are retained audit findings, not a fresh production census by integration.

| Owner | Required disposition and evidence |
| --- | --- |
| [Gapless publication](../../gapless_parquet_publication_20260901/plan.md) and [environmental retirement](../../environmental_postgres_retirement_20260904/plan.md) | Reconcile exact weather product/source identity, the uncited history floor and publication lag, published intervals, and the recorded gap. Preserve missing/unsettled/provider-refusal distinctions. Current-poll history must never be filled with reanalysis under the same identity. Any historical replacement needs its own explicit source and product contract. |
| [Reader acceptance](../../parquet_reader_cutover_acceptance_20260901/plan.md) | Add the exact 2025-04-28 regression: selecting the missing day clears the weather frame, and a delayed answer for another day cannot repaint it. Map date, details and cards must agree. Retain exact deployed-day acceptance beyond the local regression. |
| [Multiscale rendering](../../multiscale_polygon_surface_20260901/plan.md) | Preserve truthful support and refusal contracts. Sparse aggregate bins cannot be enlarged to imply a continuous surface; sampled points, declared aggregate footprints and genuinely supported surfaces remain distinct. Carry this into the production visual/conservation matrix. |

The [release packet](release-packet-20260911.md) retains the combined quality and
production gates. A passing local regression does not establish historical
coverage, repair the recorded gap or make this a forecast layer. Forecast-horizon
zero and the current source-identity boundary remain in force after integration.

The exact regression and its review/verification receipt are pending the final
combined batch. Source reconciliation, gap disposition and deployed map/details/
card agreement remain open with the owners above.
