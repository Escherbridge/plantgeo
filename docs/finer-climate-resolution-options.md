# A finer climate lane than NASA POWER 0.5°

**Scoped 2026-08-10.** Not a decision — the options, their real costs, and what each would and
would not buy. Written alongside the nine-row climate split, because that work made the ceiling
obvious: the rendering is now as good as the lattice allows, and the lattice is the limit.

## The problem this is actually about

The climate rows draw NASA POWER at **0.5°** — roughly 55 km at this latitude, 397 cells over
the whole PNW extent. The `isoline` form smooths the map, and a raster tier would smooth it
further, but neither adds information. A contour through four samples 55 km apart is an
interpolation between four samples 55 km apart however finely it is drawn. Anyone reading a
valley-floor temperature off it is reading an average that includes the ridge above them.

For agriculture that gap matters most exactly where the product is aimed: frost risk, growing
degree days and irrigation demand all turn on elevation and aspect at a scale far below 55 km.

**Smoother rendering and finer data are different asks.** The first is done. This document is
about the second.

## Options

### 1. ERA5-Land — already ingested, cheapest possible win

The soil lane already writes ERA5-Land into `agri.signal_observation` (`support_key =
'era5-land-0.1deg'`) on the 1,568-cell 0.25° `sentinel2-ndvi-0p25deg` lattice, daily
2022-04-30..2026-04-30, and `geo.soil_field_observation` already serves it. Its native
resolution is **0.1°** (~9 km); it is currently *sampled onto* a 0.25° lattice, so what is
stored is 2× finer than NASA POWER linearly and 4× by area — and the source itself has another
2.5× in it that the current lattice discards.

| | |
|---|---|
| Effort | Lowest. No new credentials, no new producer, no new schema. |
| Signals | Reanalysis meteorology — 2 m temperature, dewpoint, precipitation, radiation, wind components. Overlaps most of the eight NASA POWER signals. |
| Gain | 4× by area at the stored lattice; up to 25× if re-sampled at native 0.1°. |
| Cost | Re-sampling at 0.1° multiplies row count ~6× over the current lattice. |
| Catch | **Reanalysis, not observation.** It is a model's best estimate of the past, which is a different provenance claim from NASA POWER's — and the two must never be served as one lane. |

The honest framing: this is the option that requires the least new machinery and delivers a
real but modest improvement. It is a good first step and not the destination.

### 2. gridMET — the right answer for US agriculture

4 km, CONUS, daily, 1979–present, from the University of Idaho. Purpose-built for agricultural
and fire-weather modelling: it blends PRISM's climatology with NLDAS's daily structure.

| | |
|---|---|
| Effort | Highest of the three: a new producer, a new grid, a new lattice. |
| Signals | Max/min temperature, precipitation, humidity (max/min RH and specific humidity), wind speed **and direction**, solar radiation, plus derived ERC/BI fire indices and reference ET. |
| Gain | ~14× finer than NASA POWER linearly, ~190× by area. |
| Access | THREDDS/OPeNDAP, no key. Annual NetCDF per variable. |
| Catch | CONUS only — fine for the PNW extent, a wall if coverage ever moves north. |

Two things stand out beyond resolution. **Wind direction** is published, which the current lane
has no equivalent for at all — `WD2M` is not in the NASA POWER backfill's eight parameters, and
its absence is why wind is drawn as a scalar and no barb can honestly be rendered.
**Reference ET** is published directly, which the agri service currently has an open guard
around.

### 3. Statistical downscaling of what we already hold

Apply an elevation lapse rate to NASA POWER temperature against the DEM already loaded for
terrain, producing a finer field from the same source.

Tempting and I would not lead with it. It manufactures a value for ground nobody measured and
presents it at a resolution that invites trust. Everything in this codebase's climate lane —
`blankGroundMisreading`, the coverage notes, the `isoline` withholding on precipitation and the
pilot signals — exists to prevent exactly that. If it is ever done it belongs in the governed
model plane with its own provenance, its own `is_observed = false`, and its own validation
against station data. Never in the observation lane.

## Recommendation

**gridMET is the destination; ERA5-Land at 0.1° is the cheap step on the way.** If only one is
funded, fund gridMET — it is the only option that changes what the product can say about a
field rather than how prettily it says the same thing, and it closes the wind-direction gap as
a side effect.

Whichever lands, it is a **new lane beside NASA POWER, not a replacement of it**. The governed
views gate on `source.key`, the lattices differ, and the provenance claims differ — an
observation archive and a gridded blend are not interchangeable, and the existing
`geo.climate_field_observation` shape assumes one lane per view for that reason.

## What has to be true before any of this is worth building

1. **A grid registration.** `agri.spatial_cell` needs the new lattice with its own `grid_name`,
   the way `nasa-power-0.5-degree` and `sentinel2-ndvi-0p25deg` are registered.
2. **A serving decision.** 4 km over the PNW is on the order of 10⁴–10⁵ cells per viewport
   against NASA POWER's 397. `CLIMATE_FIELD_MAX_CELLS` is 512. A finer lane cannot be served
   as raw cells at every zoom — it needs the aggregation tiers the soil field has, and this is
   where the deferred raster tier stops being cosmetic and becomes load-bearing.
3. **A row-count estimate against the backfill budget**, using the measured cost-per-row in
   `.claude/skills/agri-pipelines`.

Point 2 is the dependency worth noting: **the finer source and the raster tier are the same
project.** A 4 km lane without an aggregated serving tier would not draw at all.
