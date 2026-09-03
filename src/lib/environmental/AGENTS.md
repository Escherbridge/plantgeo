# Environmental contracts

This directory contains browser-safe value tables and data contracts shared by
map components and server adapters. Keep network clients, credentials, database
access, and provider-specific parsing under `src/lib/server/`; browser code must
depend only on these inert contracts.

Types describe evidence returned by first-party APIs. They do not imply that a
provider is configured or that a validated warehouse release is available.

## §soil-field

`soil-field.ts` is the value vocabulary for **all three** ERA5-Land layers — volumetric
soil water, soil temperature and daily-max vapor pressure deficit: the ECMWF depths and
their `agri.signal_observation` signal names, each measure's band breaks and colour ramp,
its unit, and the attribution CC-BY obliges us to publish wherever the values are drawn. It
supersedes `soil-moisture.ts` (removed 2026-08-06), which held the moisture half of the
same tables.

It lives here, not under `src/lib/server/`, because three consumers need it and only one of
them is a server: the read model classifies features with it, `SoilFieldLayer` builds its
`fill` expression from it, and `SoilDetails` renders its legend and its depth selector from
it. Deriving all three from one table is what stops a colour on the map from disagreeing
with a colour in the legend. Nothing here touches the network or the database, so it stays
browser-safe under the rule above.

**One depth vocabulary, three measures.** ERA5-Land uses the same four layer boundaries
(0–7, 7–28, 28–100, 100–255 cm) for temperature as for volumetric water, so `SoilFieldDepth`
is one union and each measure declares which of them it publishes. Moisture publishes three:
ECMWF defines a fourth volumetric-water layer and the Open-Meteo lane does not fetch it, so
it is absent rather than offered and answering empty. Temperature publishes all four. A
depth one measure does not offer resolves to that measure's first layer rather than throwing
— a stale store value or a replayed IndexedDB entry must degrade to a drawn field.

VPD is not soil state at all — Open-Meteo derives daily-max vapour pressure deficit from
2 m temperature and humidity on the same era5_land reanalysis — but it rides the same
lattice, support key and provenance, so it is a third measure of this vocabulary rather
than a fourth serving surface. It declares a single `surface` pseudo-depth (labelled
"Near-surface air (2 m, daily max)", centimetre fields degenerate at 0) so the shared
store/panel/query shape needs no VPD special case; the fallback rule above makes any stale
depth value resolve to it.

**The band tables are not equally earned, and the file says so.** Moisture's breaks are
0.05-wide over 0.05–0.35 m³/m³, chosen against the lane's measured spread (0.023–0.430,
mean ~0.21 on production 2026-08-05): that separates the arid Snake River Plain from the
Coast Range without collapsing the middle of the distribution into one band. Its ramp is
ColorBrewer BrBG, brown dry to teal wet — diverging, because the useful reading is "drier or
wetter than typical". Temperature's 5 °C breaks over −5..25 °C are **not** measured against
this lane: the backfill had written no rows when they were chosen, so they are anchored
physically instead (0 °C is the freeze boundary) and are marked for re-derivation against
the realised distribution. Its ramp is RdYlBu reversed, so cold reads cold. VPD's 0.5 kPa
breaks over 0.5–3.5 kPa are measured like moisture's (2,149,140 accepted rows on production
2026-08-08: 0–8.84 kPa, median 0.77, p90 3.21), and its ramp is sequential YlOrBr rather
than diverging — VPD has a true zero and the fire-weather reading is "how strong is the
drying", a monotone question with no typical-value midpoint.

**Range labels read "a to b", not "a – b".** Temperature bands are signed, and "−5 – 0" asks
a reader to tell a range separator from a minus sign. One rule, applied to both measures.

## §climate-field

`climate-field.ts` is the same kind of table for the **NASA POWER** lane: nine client-facing
signals (eight meteorology, three of them statistics of one air-temperature quantity, plus
three pilot soil-wetness signals) over the 397-cell `nasa-power-0.5-degree` lattice, with
`support_key = 'surface'` and the attribution `NASA POWER (NASA LaRC)`. Same three consumers,
same reason it is not under `src/lib/server/`.

**The wire never carries a `signal_name`.** The client sends a `ClimateFieldSignalId` and
`climateFieldSignalName()` resolves it, which is what keeps the warehouse's naming out of the
browser bundle and out of the tRPC schema. `air-temperature` is the one signal with variants
— NASA POWER publishes mean, max and min over the same cells and days — and a variant no
signal offers degrades to that signal's single reading rather than throwing, exactly as a
stale soil depth does.

**Its band builder differs from `soil-field.ts`'s in one place, deliberately.** Soil's open
tails sit half an interior band beyond the outer break, which is well defined because every
soil measure's interior bands are uniform. Precipitation's are not (0.9/1.5/2.5/5/15 mm wide,
because daily rainfall here is mostly zeros and a uniform scale would put almost every cell
in one band), and that rule would place its bottom stop at −0.35 mm/day. Each signal declares
a value DOMAIN instead and the tails take its bounds, so a bounded quantity — precipitation,
relative humidity, a saturation fraction — never gets an interpolation stop outside the
values it can hold. Domains are the measured p02–p98 of each signal on production.

**The three soil-wetness signals are a pilot and say so in their own labels.** 4 cells of 397.
They carry a `coverageNote` the panel and the legend both render, because a reader who selects
a signal and sees four squares must be told that is the coverage rather than an outage.

**`resolveClimateRenderForm` is no longer the last word on the drawn form.** It answers "may this
SIGNAL be drawn this way", which is a statement about honesty per quantity — no contour across
daily rainfall, none across a four-cell pilot — and it is still the only place that rule lives.
Above it now sits `tierRenderForm` (`server/services/parquet-climate-field.ts`), which answers
"may this RUNG be drawn this way" from `LAYER_RENDER_CONTRACT`: `isoband` is permitted at the
coarse and middle bands only, so a signal whose own list opens on `isoline` is served filled at
z13 and reports that back in `renderForm`. Two gates rather than one because they answer different
questions, and collapsing them would make a per-signal rule depend on the camera.

**The lattice this table calls `nasa-power-0.5-degree` is SAMPLED every whole degree.** The
`support_key` and the grid name are the source product's resolution; `CLIMATE_FIELD_LATTICE_ROWS`
steps by one degree, so half the ground in each axis carries no sample. The serving side draws the
one-degree nearest-sample cell rather than a half-degree square with background between the
squares — see `src/lib/server/services/AGENTS.md` §tessellated-support for why that is permitted
here and forbidden for `vegetation`.

## §parquet-presentation

`parquet-presentation.ts` and `parquet-fire-presentation.ts` are the browser mirrors of the
Parquet readers plus the functions that turn a served window into drawable GeoJSON. They are
the last place the SHAPE of a feature is decided, so the render contract's rules live here
rather than in the three layer components.

**`support` is required on the wire and optional in the mirror.** Every aggregate row
`parquet-trpc-readers.ts` returns declares an `AggregateEnvelopeSupport`. The browser mirrors
type it `support?:` anyway, so a payload that arrives without one degrades to the marker form
instead of throwing through a React render — the same rule `servingZoomTierForMapZoom` follows
for an unservable zoom, and the same reason the readers' four terminal states are values rather
than exceptions. What the degradation may never do is fill the gap: a missing cell size yields
a Point, never a nominal square, because a fabricated footprint is the fire-perimeter claim the
whole track exists to prevent.

**The form each presenter draws is a named exported constant, checked against the contract in
tests, not a string at a call site.** `FIRE_CELL_DRAWN_FORM` is `aggregate_cell` at every rung
(FIRMS publishes no raw one), and `VEGETATION_CELL_DRAWN_FORM` is `tessellated_cell`, the only
form `LAYER_RENDER_CONTRACT.vegetation` permits at any band. The reader may label a vegetation
envelope with the generic `aggregate_cell`; the DRAWN form is the contract's, and the geometry
is identical either way — the square the envelope itself declares.

**The envelope's rung beats the caller's.** `presentParquetFireDetections` still takes the
caller's latched tier, because `useParquetFireDetections` holds the last one that actually
landed and a retained frame must not be relabelled with a request still in flight. But each
cell's own `support.zoomTier` wins where present: a retained frame's cells carry the rung they
were really aggregated at, which is a stronger claim than the latch and needs no bookkeeping.
