"""Measured PlantGeo lane facts, 2026-08-25.

Every number and every basis string below came from
`agri-service data parquet-drain --dry-run` (both selections) run against the
production bucket on 2026-08-25. Nothing is inferred or remembered.
"""

LANES = {
    "signal": dict(
        nature="daily_series", floor="2022-04-30", cadence=1, lag=9, fc=True,
        base=1560, absent=10, missing=1, unfinished=0, ladder_ok=1338, ladder_bad=222,
        basis=(
            "Whole plane measured 2022-04-30..2026-08-06 across BOTH producers. Lag 9 is "
            "ERA5-Land's PUBLICATION_LAG_DAYS; NASA POWER's is 5. The LARGER is deliberate -- at "
            "lag 5 the four newest days would be declared missing while ERA5-Land has genuinely "
            "not published them."
        ),
        cols="cell_longitude, cell_latitude",
    ),
    "vegetation": dict(
        nature="daily_series", floor="2022-08-05", cadence=1, lag=7, fc=True,
        base=1195, absent=280, missing=1, unfinished=0, ladder_ok=990, ladder_bad=205,
        basis=(
            "Governed forecastable plane holds 2022-08-05..2026-08-04, the deepest record of any "
            "lane. Lag 7 is the MEASURED median gap between observation days -- worse than "
            "Sentinel-2's nominal 5-day revisit because cloud screening removes scenes. Most days "
            "in the window are correctly a governed absence."
        ),
        cols="cell_longitude, cell_latitude",
    ),
    "sensors": dict(
        nature="daily_series", floor="2026-07-29", cadence=1, lag=1, fc=True,
        base=26, absent=1, missing=1, unfinished=0, ladder_ok=1, ladder_bad=25,
        basis=(
            "NWS keeps a rolling ~6-day window and NO deeper archive exists. The whole record is "
            "what this producer accreted since 2026-08-04 plus its first run's ~6-day reach. "
            "geo.features is append-only for this lane, so the floor is static even though the "
            "SOURCE's is not."
        ),
        cols="station_longitude, station_latitude",
    ),
    "fire-perimeters": dict(
        nature="daily_series", floor="2025-07-28", cadence=1, lag=1, fc=False,
        base=45, absent=287, missing=62, unfinished=1, ladder_ok=45, ladder_bad=0,
        basis=(
            "A daily series is ALLOWED to decline a forecast -- the nature is the ceiling, the "
            "shipped forecaster is the claim, and here the claim is deliberately absent. What is "
            "held is the residue of the hourly _Current poller, oldest isolated row 2025-07-28. "
            "The declared 2020-01-01 floor (WFIGS_PERIMETER_HISTORY_EARLIEST) is "
            "documentation-derived, has NO fetcher wired, and would invent ~2,000 phantom "
            "gap-days."
        ),
        cols=None,
    ),
    "fire-detections": dict(
        nature="daily_series", floor="2000-11-02", cadence=1, lag=2, fc=True,
        base=8357, absent=1069, missing=1, unfinished=0, ladder_ok=8357, ladder_bad=0,
        basis=(
            "Production's sampled minimum observedAt is 2000-11-02, one day after the archive "
            "walk's own floor. Lag 2 from FIRMS_DAY_RANGE rolling NRT lookback (default 2, "
            "clamped 1-5). This is the DEEPEST window of any lane -- ~9,400 days -- and is "
            "exactly what the newest-first ordering exists to keep tolerable."
        ),
        cols=None,
    ),
    "drought": dict(
        nature="release_series", floor="2022-08-09", cadence=7, lag=4, fc=False,
        base=209, absent=2, missing=0, unfinished=0, ladder_ok=209, ladder_bad=0,
        basis=(
            "A USDM map is a dated publication; valid_date IS the release's own fact rather than "
            "a day anyone observed. MEASURED against production: min=2022-08-09, max=2026-08-18, "
            "209 distinct releases, 1,045 rows. The ingest code's USDM_ARCHIVE_START of "
            "2000-01-04 is an ARCHIVE CAPABILITY, not what production holds -- using it would "
            "invent ~1,100 phantom weeks. cadence 7 because valid_date is always a Tuesday and "
            "the floor is a Tuesday, so the step lands on real release days."
        ),
        cols=None,
    ),
    "burn-severity": dict(
        nature="release_series", floor="2020-11-24", cadence=1, lag=7, fc=False,
        base=4, absent=2089, missing=2, unfinished=1, ladder_ok=4, ladder_bad=0,
        basis=(
            "MTBS publishes fire-year cohorts quarterly and each release IS a dated fact -- five "
            "of them, 2020-11-24..2024-08-22. cadence_days stays 1 DELIBERATELY, unlike "
            "drought's 7: those five dates do not sit on any fixed step from the floor, so a "
            "cadence above one would step straight past real releases. The ~2,000 honest absence "
            "markers this costs are the PRICE of an irregular release series, not a bug."
        ),
        cols=None,
    ),
    "water-gauges": dict(
        nature="daily_series", floor="2026-05-24", cadence=1, lag=2, fc=True,
        base=91, absent=0, missing=2, unfinished=0, ladder_ok=91, ladder_bad=0,
        basis=(
            "The DENSE record starts 2026-05-24. The code floor USGS_DAILY_VALUES_EARLIEST = "
            "2022-08-05 is explicitly BORROWED from the vegetation layer, not source-imposed, and "
            "nothing confirms the archive walk has reached it -- using it would invent ~1,400 "
            "phantom gap-days. The bare min(observed_day) of 1990-10-01 is documented as a TRAP. "
            "Lag 2 is UNVERIFIED for this bbox."
        ),
        cols=None,
    ),
    "weather-observations": dict(
        nature="daily_series", floor="2026-08-01", cadence=1, lag=2, fc=False,
        base=20, absent=2, missing=2, unfinished=0, ladder_ok=20, ladder_bad=0,
        basis=(
            "FALLBACK -- NOT DECLARED ANYWHERE, AND THE GUESS IS DELIBERATELY SHALLOW. "
            "docs/lanes/weather-observations.md describes the NASA POWER / ERA5-Land archive, "
            "which is the SIGNAL stream, not this lane. The producer THIS lane exports "
            "(ingest/open_meteo.py's WEATHER_LAYER current-conditions poll into geo.features) has "
            "NO contract content at all: no declared cadence, horizon, historical depth or "
            "known-gaps list. 2026-08-01 is a conservative recent floor chosen so a wrong guess "
            "costs a few dozen phantom gap-days instead of thousands."
        ),
        cols=None,
    ),
    "calendar": dict(
        nature="static_lookup", floor="2000-11-02", cadence=1, lag=0, fc=False,
        base=1, absent=0, missing=0, unfinished=0, ladder_ok=1, ladder_bad=0,
        basis=(
            "The ONE lane with no source system. The floor is DERIVED as min(history_floor) "
            "across the twelve database-backed lanes -- 2000-11-02, which is fire-detections' -- "
            "so every day any lane can key to the dimension is in it. Each version covers its own "
            "day plus 800 days and must reach today plus 400, so a 30-day horizon from any as-of "
            "date always resolves and the lane regenerates roughly once a year, not once a day."
        ),
        cols=None,
    ),
    "watersheds": dict(
        nature="static_lookup", floor="2026-08-07", cadence=1, lag=0, fc=False,
        base=1, absent=0, missing=0, unfinished=0, ladder_ok=1, ladder_bad=0,
        basis=(
            "Exactly ONE load day exists, 2026-08-07, all 9,396 rows, and the boundaries are a "
            "snapshot rather than a series. A HUC12 boundary is a reference fact WITH A VERSION, "
            "so the partition day comes from sql/pipeline/lane_watermark_watersheds.sql -- "
            "geo.features' change-gated updated_at/created_at for this layer -- and the floor is "
            "INERT."
        ),
        cols=None,
    ),
    "evacuation-zones": dict(
        nature="static_lookup", floor="2025-04-14", cadence=1, lag=0, fc=False,
        base=1, absent=0, missing=0, unfinished=1, ladder_ok=1, ladder_bad=0,
        basis=(
            "HistoryCapability(supported=False) -- Oregon OEM publishes CURRENT STATE ONLY and no "
            "past evacuation level is reconstructable. The floor is the sampled observedAt span's "
            "start and is INERT: this lane's partition day comes from "
            "sql/pipeline/lane_watermark_evacuation_zones.sql, not from the floor and not from "
            "the cron's run date."
        ),
        cols=None,
    ),
    "soil-survey": dict(
        nature="static_lookup", floor="2025-08-26", cadence=1, lag=0, fc=False,
        base=0, absent=0, missing=0, unfinished=1, ladder_ok=0, ladder_bad=0,
        basis=(
            "Vintage-only, NOT a daily series -- one live vintage per delineation, keyed by "
            "survey-area publication. The floor is the measured saverest span start "
            "(2025-08-26..2026-03-19) and is INERT: the partition day comes from "
            "sql/pipeline/lane_watermark_soil_survey.sql, which takes the newer of SSURGO's own "
            "saverest vintage and the day this warehouse's lazily-warmed published set last grew."
        ),
        cols=None,
    ),
}
