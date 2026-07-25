# NASA POWER daily API research

- URL: https://power.larc.nasa.gov/docs/services/api/temporal/daily/
- Retrieved: 2026-07-20
- Intent: validate the initial historical meteorology source for the PlantGeo four-year backfill.

NASA POWER's daily temporal API returns analysis-ready daily solar and meteorological time series. It supports JSON, CSV, NetCDF, and ASCII responses; the point endpoint accepts a longitude, latitude, start date, end date, and up to 20 parameters. Daily data are available from 1981-01-01 to near real time. The service documents a 0.5-degree by 0.5-degree global grid and cautions clients not to repeat requests for the same grid cell. The daily endpoint defaults to local solar time, so PlantGeo must send `time-standard=UTC` for its canonical time contract.

Implementation implications: cache and deduplicate by source grid cell, request daily UTC data, persist provider metadata and requested parameter set, and treat HTTP 429 as retryable with conservative backoff.
