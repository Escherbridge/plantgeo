# Environmental contracts

This directory contains browser-safe value tables and data contracts shared by
map components and server adapters. Keep network clients, credentials, database
access, and provider-specific parsing under `src/lib/server/`; browser code must
depend only on these inert contracts.

Types describe evidence returned by first-party APIs. They do not imply that a
provider is configured or that a validated warehouse release is available.

## §soil-moisture

`soil-moisture.ts` is the value vocabulary for the ERA5-Land layer: the three ECMWF depths
and their `agri.signal_observation` signal names, the band breaks, the colour ramp, the
unit, and the attribution CC-BY obliges us to publish wherever the values are drawn.

It lives here, not under `src/lib/server/`, because three consumers need it and only one of
them is a server: the read model classifies features with it, `SoilMoistureLayer` builds its
`fill` expression from it, and `SoilPanel` renders its legend from it. Deriving all three
from one table is what stops a colour on the map from disagreeing with a colour in the
legend. Nothing here touches the network or the database, so it stays browser-safe under
the rule above.

The breaks are 0.05-wide over 0.05–0.35 m³/m³, chosen against the lane's measured spread
(0.023–0.430, mean ~0.21 on production 2026-08-05) rather than a textbook scale: that
separates the arid Snake River Plain from the Coast Range without collapsing the middle of
the distribution into one band. The ramp is ColorBrewer BrBG, brown dry to teal wet —
diverging, because the useful reading is "drier or wetter than typical".
