---
type: evidence-receipt
---

# Frozen-input rehash receipt — 2026-08-14

Produced by `services/agri-data-service/src/agri_data_service/execution/public_evaluation_rehash.py`,
invoked as:

```
uv run python -m agri_data_service.execution.public_evaluation_rehash
```

This module reads each frozen input's bytes from disk, computes SHA-256 with
`agri_data_service.foundation.canonical.sha256_digest`, and compares the digest
against the value bound in `spec.md`'s "Immutable inputs" table. Neither digest
is asserted without reading the file; a missing file raises `FileNotFoundError`
rather than reporting a false match.

## Result: both frozen inputs verify. `all_match: true`.

```json
{
  "all_match": true,
  "inputs": [
    {
      "label": "ghisaconus_csv_v1",
      "path": "C:\\tmp\\plantgeo-kaggle-ghisaconus-v1\\GHISACONUS_2008_001_speclib.csv",
      "expected_sha256": "e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5",
      "actual_sha256": "e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5",
      "byte_count": 11540638,
      "matches": true,
      "checked_at": "2026-08-14T19:41:25.549569+00:00"
    },
    {
      "label": "frozen_forecast_manifest_v1",
      "path": "C:\\tmp\\plantgeo-frozen-forecast-20260726\\manifest.json",
      "expected_sha256": "1bb6a6a707b432f2036edba86a426a32c1c04304b350af4caaec14a48cb20d09",
      "actual_sha256": "1bb6a6a707b432f2036edba86a426a32c1c04304b350af4caaec14a48cb20d09",
      "byte_count": 2394,
      "matches": true,
      "checked_at": "2026-08-14T19:41:25.551567+00:00"
    }
  ]
}
```

## What this proves, and what it does not

Both frozen inputs pinned in `spec.md` ("Immutable inputs") are byte-identical,
right now, to what the crop-spectrum and forecast-lane evidence in
`conductor/review-packet-20260726/blockers.md` was produced from. Neither has
silently drifted, been substituted, or been re-exported since 2026-07-26.

This does **not** change either lane's decision. The rehash only proves the
inputs are still the ones reviewed; it says nothing about whether those inputs
are *sufficient*. `blockers.md` already established, independently of file
integrity, that:

- the crop lane's rice class has only 2 independent held-out images against a
  3-image requirement, and
- the forecast lane's frozen export recorded every source row after every
  simulated forecast origin (total availability leakage).

Both conditions are properties of the data's content and timing, not its
checksum, so a clean rehash does not unblock either lane. See
`decision-record-2026-08-14.md` for the lane decisions this receipt feeds.
