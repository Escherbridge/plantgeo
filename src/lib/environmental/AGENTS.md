# Environmental contracts

This directory contains browser-safe value tables and data contracts shared by
map components and server adapters. Keep network clients, credentials, database
access, and provider-specific parsing under `src/lib/server/`; browser code must
depend only on these inert contracts.

Types describe evidence returned by first-party APIs. They do not imply that a
provider is configured or that a validated warehouse release is available.

## §soil-field

`soil-field.ts` is the value vocabulary for **both** ERA5-Land layers — volumetric soil
water and soil temperature: the ECMWF depths and their `agri.signal_observation` signal
names, each measure's band breaks and colour ramp, its unit, and the attribution CC-BY
obliges us to publish wherever the values are drawn. It supersedes `soil-moisture.ts`
(removed 2026-08-06), which held the moisture half of the same tables.

It lives here, not under `src/lib/server/`, because three consumers need it and only one of
them is a server: the read model classifies features with it, `SoilFieldLayer` builds its
`fill` expression from it, and `SoilPanel` renders its legend and its depth selector from
it. Deriving all three from one table is what stops a colour on the map from disagreeing
with a colour in the legend. Nothing here touches the network or the database, so it stays
browser-safe under the rule above.

**One depth vocabulary, two measures.** ERA5-Land uses the same four layer boundaries
(0–7, 7–28, 28–100, 100–255 cm) for temperature as for volumetric water, so `SoilFieldDepth`
is one union and each measure declares which of them it publishes. Moisture publishes three:
ECMWF defines a fourth volumetric-water layer and the Open-Meteo lane does not fetch it, so
it is absent rather than offered and answering empty. Temperature publishes all four. A
depth one measure does not offer resolves to that measure's first layer rather than throwing
— a stale store value or a replayed IndexedDB entry must degrade to a drawn field.

**The band tables are not equally earned, and the file says so.** Moisture's breaks are
0.05-wide over 0.05–0.35 m³/m³, chosen against the lane's measured spread (0.023–0.430,
mean ~0.21 on production 2026-08-05): that separates the arid Snake River Plain from the
Coast Range without collapsing the middle of the distribution into one band. Its ramp is
ColorBrewer BrBG, brown dry to teal wet — diverging, because the useful reading is "drier or
wetter than typical". Temperature's 5 °C breaks over −5..25 °C are **not** measured against
this lane: the backfill had written no rows when they were chosen, so they are anchored
physically instead (0 °C is the freeze boundary) and are marked for re-derivation against
the realised distribution. Its ramp is RdYlBu reversed, so cold reads cold.

**Range labels read "a to b", not "a – b".** Temperature bands are signed, and "−5 – 0" asks
a reader to tell a range separator from a minus sign. One rule, applied to both measures.
