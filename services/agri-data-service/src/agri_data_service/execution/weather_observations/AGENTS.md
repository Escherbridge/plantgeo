# `execution/weather_observations` — the first domain package

## Responsibility
The governed historical weather archive backing `agri.signal_observation`: NASA POWER daily point
data (`nasa_power.py`, was `historical_backfill.py`) and the Open-Meteo ERA5-Land archive
(`era5_land.py`, was `historical_open_meteo.py`). This is the **template** for the other ten
domains — RUNBOOK §0.25.5 step 4 asks for one domain proven end to end before the shape repeats.

Lane contract: [`docs/lanes/weather-observations.md`](../../../../../../docs/lanes/weather-observations.md).
Read it before changing anything here; six lane contracts corrected claims the runbook asserted,
and this one corrects three.

## The boundary, and what it cost to get here
A domain package may import `execution` **root** primitives (`contracts.py`, `backfill_types.py`,
`coverage_*`, `source_ingestion.py`, `open_meteo_lane.py`). It may **not** import another
`execution.<domain>` package. `tests/test_layer_import_contract.py` enforces this via
`SUBPACKAGE_FORBIDDEN_IMPORTS`; the rule went in with this package rather than after ten of them
existed, because a convention with no test is a convention that has already been broken somewhere.

Making that boundary true required moving four value types out first. `historical_backfill.py`
owned `AnalysisGridCell`, `HistoricalBackfillWindow`, `HistoricalSignalObservation` and
`HistoricalCoverageAudit`, and five sibling domains — CAMS, GloFAS, CEMS, AgERA5, ERA5 — imported
them from there. Moving NASA into a domain package without that extraction would have made every
one of those five import this one. They now live at `execution/backfill_types.py`.

## Two producers, one lane, and they must not be blended
- **NASA POWER** (`nasa_power.py`) — keyless, 397 cells on `nasa-power-0.5-degree`, 11 parameters,
  `support_key='surface'`, publication lag **5 days**.
- **Open-Meteo ERA5-Land** (`era5_land.py`) — an *intermediary redistributor* of ECMWF ERA5-Land,
  not a first-party CDS receipt. `models=era5_land` is pinned as a `Literal` because the
  endpoint's undeclared default is the coarser `era5`, which answers silently with the wrong
  product. `support_key='era5-land-0.1deg'`, publication lag **9 days**, 1,470 of 1,568 cells
  (the missing 98 are Pacific-edge water and are not a gap to fill).

The two never collide on a cell-day because they carry different `support_key`s. NASA's
soil-*wetness* (degree of saturation) and ERA5-Land's soil-*water-content* (m³/m³) are different
physical quantities under distinct `signal_name`s — a naming discipline to preserve, not a
duplication to merge.

## Traps that will otherwise be rediscovered
- **`surface_shortwave_radiation` has zero NASA rows for July 2026** while every sibling signal has
  12,307. Unexplained, under contract, and the lane contract's designated first validation case.
  Its real publication lag is ~2 months, not the blanket 5 days — do not apply the constant.
- **The declared NASA horizon is 98 days narrower than what production holds** (2022-08-06 declared
  vs 2022-04-30 measured). Deliberate conservatism; widening it is an owner call.
- **`historical_era5.py` (CDS-direct) is superseded and never persisted a row.** It stays at
  `execution/` root as an integration template for genuinely CDS-only products. Do not resurrect
  its plans, and do not move it here — it is not this domain's producer.
- **The name `weather-observations` is overloaded.** `ingest/open_meteo.py` binds a `WEATHER_LAYER`
  of the same name that pulls *current conditions* from a different endpoint into `geo.features`,
  not this plane. Confirm which producer a change is aimed at before writing code.

## Still at `execution/` root, deliberately
`historical_writer/nasa.py` and `historical_writer/open_meteo.py` are this domain's writers but
remain inside the shared `historical_writer/` package, which is already organised per source over
shared internals (`_shared.py`, `_results.py`, `_release_sets.py`) that CAMS, GloFAS and USDM also
use. Splitting it would either duplicate those internals or export private modules across
packages. Revisit when a second domain moves and the shared surface is measured, not before.
