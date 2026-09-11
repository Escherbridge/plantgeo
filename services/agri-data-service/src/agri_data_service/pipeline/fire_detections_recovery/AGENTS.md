# FIRMS exact-day archive preparation

This product package replays supplied source responses into a local candidate. It imports the
retained pure FIRMS parser/identity/processing precedence and direct Arrow normalizer; it never
constructs a `FeatureWriter`, opens PostgreSQL, calls an object store, or authors queue, terminal,
absence or availability state. The old archive caller and its effective execution ownership are
unchanged. The direct forward floor remains 2026-08-25.

## Source contract and bounds

`source-manifest.json` conforms to `FireSourceCapture` in `models.py`. Pass its independently
recorded complete SHA-256 to `prepare_fire_recovery`; each captured response lives under
`responses/<complete-sha256>.csv`. The manifest has exact acquisition day and WGS84 bbox,
timezone-aware capture start/end clocks, a complete HTTP 200 availability response and one
complete HTTP 200 product response for every applicable product. Response hashes cover entire
entity bytes, never a range sample. Clocks attest capture time, not historic data availability.

One unit is one day on/after 2000-11-01, at most eight requests (one availability plus seven
registered products), a ten-minute capture interval, 64 KiB availability, 16 MiB per product,
64 KiB manifest and 50,000 raw detections across the complete constellation. The manifest omits
URLs, headers, credentials and free-form capture notes; credentials must never enter file paths,
CLI arguments, receipts or logs. This replay tool does not read credentials or fetch sources.

Availability windows select products from the existing declared FIRMS history constellation.
Missing applicable responses, malformed rows, non-finite values, off-day/out-of-bbox detections,
duplicate availability claims and conflicting equal-priority native identities refuse the unit.
Validated decoded CSV cells feed the legacy pure parser, preserving quoted radiometry/confidence;
original source bytes remain unchanged. Existing standard-processing-over-NRT precedence applies
only after conflict validation. Exact duplicates collapse with the original byte evidence retained.
Zero detections do not create a data or absence candidate.

## Candidate and admission boundary

`prepare_fire_detections_recovery.py` takes `--source-root`, `--source-manifest-sha256` and a new
`--output` directory. All source validation and four-rung derivation finish before output creation.
The directory includes original response bytes, source manifest, z0/z5/z9/z13 Parquet files with
the registered Arrow schema, per-part full hashes and row counts, and `candidate.json` plus its
hash. Every rung conserves deduplicated detection count. The candidate has `apply=false`, no
completion marker, no availability, and `upstream_population_complete=false`.

A full-hash operator capture attestation establishes what this local replay was supplied; it
cannot authenticate the upstream server, prove a complete governed AOI, or establish global fire
population. The generic recovery owner must bind an audited full source request/capture unit and
its AOI to the governed historical lane-day before publication. A small bbox candidate cannot
replace the layer day. Historic cell aggregates cannot be added to an existing aggregate blindly:
the owner must compare the complete day constellation, native source identity and prior complete
ladder under the lane-day barrier, then replace/finalize all four rungs through its shared driver.

The prior complete ladder and absence/availability generation must be pinned before admission.
Rollback restores those exact previous generation references under the same barrier and retains
the immutable candidate bytes. A partial local directory without `candidate.json` is not a
successful candidate. Neither script supplies an apply or rollback mutation operation.

## Verification

`tests/parquet/test_fire_detections_recovery.py` covers exact schema at every rung, original bytes,
quoted CSV values, constellation undercoverage, same-priority conflicts, existing SP precedence,
malformed/empty input and complete-hash tampering. Author all product fixes before the root's
single integrated verification sweep.

FIRMS source conventions remain in `../../ingest/AGENTS.md` and `../direct/AGENTS.md`.
The authoritative product availability
response decides the available historical products; dates are not invented from filenames.
