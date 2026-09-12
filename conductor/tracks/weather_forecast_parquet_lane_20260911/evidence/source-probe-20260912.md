---
type: source-probe
recorded_on: 2026-09-12
status: evidence_only_not_admitted
---

# Bounded Open-Meteo single-run probe

Read-only HTTP 200 response from [Open-Meteo Single Runs](https://open-meteo.com/en/docs/single-runs-api),
requested `gfs_global`, initialization `2026-09-11T00:00`, point `(40,-105)`,
one day, UTC, `temperature_2m`. Attribution: Open-Meteo / NOAA GFS candidate;
[provider licence](https://open-meteo.com/en/licence) and
[terms](https://open-meteo.com/en/terms) apply to provider data.
This evidence is not a published PlantGeo forecast or fixture input.

The exact JSON response bytes (without an added trailing newline) have SHA-256
`099d00cf256302968ba9461323a3448d9e6a027134572e143072ae935ca0d8f5`.

```json
{"latitude":40.006516,"longitude":-105.0,"generationtime_ms":0.3050565719604492,"utc_offset_seconds":0,"timezone":"GMT","timezone_abbreviation":"GMT","elevation":1592.0,"hourly_units":{"time":"iso8601","temperature_2m":"°C"},"hourly":{"time":["2026-09-11T00:00","2026-09-11T01:00","2026-09-11T02:00","2026-09-11T03:00","2026-09-11T04:00","2026-09-11T05:00","2026-09-11T06:00","2026-09-11T07:00","2026-09-11T08:00","2026-09-11T09:00","2026-09-11T10:00","2026-09-11T11:00","2026-09-11T12:00","2026-09-11T13:00","2026-09-11T14:00","2026-09-11T15:00","2026-09-11T16:00","2026-09-11T17:00","2026-09-11T18:00","2026-09-11T19:00","2026-09-11T20:00","2026-09-11T21:00","2026-09-11T22:00","2026-09-11T23:00"],"temperature_2m":[30.5,26.3,23.4,21.8,22.0,21.7,20.8,19.0,18.3,17.6,16.6,15.7,15.4,16.0,19.5,22.5,25.2,28.1,30.5,32.0,32.1,32.4,32.9,27.5]}}
```
