---
type: validation-report
---
# Scheduled historical water turn

Read-only inspection of executor deployment `a5010e4d-ded0-43f1-8646-1f07201f453d`, which serves release `8119cc05`. No historical run was forced.

The tick at `2026-11-03T06:50:23.821472300Z` dispatched `water-gauges-daily:gap-fill` for its scheduled `06:50` bucket, run `9507ae66-4f4e-4bfc-ae14-076f6d415869`. At `06:51:26.382848827Z`, the ledger still reported running. This is execution evidence, not completion or automatic target-idle evidence.

At approximately `06:52:22Z`, the run emitted repeated `water_gauges_daily_identity_conflict` events for historical gauge/day/statistic identities. One example is `USGS-12010000:1997-01-03:00003`, with time-series IDs `06e178679c8a4860bb717e81d05fc2e3` and `afd32209ce104f71b49d43f73a06964e`. A bounded log capture contains 151 such events; it is a truncated sample, not a total conflict count or a count of provider requests.

Independent source inspection confirms that conflicting gauge/day/statistic rows are dropped entirely, while clean gauges on the day may be published. `usgs_water_data.day_view` includes time-series identity in its comparison, so equal numeric values from distinct series still conflict. `settle` returns `Written(source_resolved=False)` with the conflict count; the runner retains `source_unresolved` debt rather than declaring complete support. This protects the completeness claim but leaves a real source-selection question unresolved.

Gap-fill holds unresolved days behind other missing days and re-asks them only after those holes drain; an unchanged unresolved response is not republished. This is not an immediate retry per warning. Recurring provider cost remains possible when unresolved days become eligible or rolling revisions select them. Both row generation and settlement call `day_view`, so warning count is not even a unique conflict count. Existing source documentation already describes this gauge's multiple-series issue in 1990–1991; this capture shows later affected dates without resolving which series should be authoritative.

The run completed at approximately `2026-11-03T07:20:24Z` with process exit zero but semantic outcome `incomplete`. Its **before-turn** target assessment counted 7,495 full-support data days out of 13,168 (56.9183%), 5,673 incomplete days, zero wholly missing days and zero governed-absence days, spanning 1990-09-30 through 2026-10-18. The configured 100% target remained unmet. These are full-day support metrics, not the fraction of all individual gauge readings retained.

The turn sent 104 HTTP requests (all 2xx, no 429/4xx/5xx or transport failures), within its 112-request cap. It fanned out 366 days, wrote 101 days, left 73 unchanged and reported 366 dropped identity-conflict rows. It received 154,243,910 bytes and wrote 5,026,849 bytes. Of 1,802.835 elapsed seconds, census took 19.671, fetch 50.768, settlement 63.592 and writing 1,668.533 seconds. Publication, rather than upstream waiting, dominated this observed turn.

The executor emitted an explicit lane-incomplete event for 5,673 days. A later scheduler tick calls the settled execution bucket `succeeded` and `not_due`; that scheduling status is not complete data coverage. Automatic target-idle/resume remains test-proven; this operational run proves the unmet-target branch, not the reached-target branch. No forced run was used.

Local evidence: `.omc/research/water-0650-completed.json`, `.omc/research/water-0650-running.json`; preceding scheduler evidence: `.omc/research/identity-checkpoint-executor-logs.json`.

## Recurring assessment cost and time boundary

Independent inspection found that target assessment loads the full configured census before applying fill-day limits. `runner/census.py` lists each year for each stream and ladder tier; `runner/completeness.py` reads one proof object per stream/year. The 1990–2026 range therefore repeats 37 years of tier listings, including pagination where necessary, plus 37 proof reads per stream. This is object-store I/O, not USGS request quota, and is not one object read per calendar day. The coverage arithmetic itself runs in memory.

The configured 1,800 seconds is not a hard whole-turn wall. Fetches use a per-unit timeout capped by the remaining fetch deadline; publication checks its write deadline before beginning each day. Synchronous census work has no turn-deadline checks, and publication/repair operations already started can finish after the deadline. Do not describe the run as strictly capped at 30 minutes. A follow-up cost policy must distinguish provider acquisition, coverage-index assessment, unresolved-source rechecks and source-free publication repair.

## Official series metadata follow-up

Two bounded USGS metadata-item reads confirmed that both conflicting series are marked `Primary` and both cover the example date, 1997-01-03. The [first series](https://api.waterdata.usgs.gov/ogcapi/v1/collections/time-series-metadata/items/06e178679c8a4860bb717e81d05fc2e3?f=json) has no named sublocation and has a parent series; the [second](https://api.waterdata.usgs.gov/ogcapi/v1/collections/time-series-metadata/items/afd32209ce104f71b49d43f73a06964e?f=json) names `STILLING WELL` and has no parent. Both describe daily mean discharge at the same monitoring location. A Primary-only filter therefore cannot resolve this conflict.

The [USGS metadata schema](https://api.waterdata.usgs.gov/ogcapi/v0/collections/time-series-metadata/schema?f=html) documents sublocation and web description as distinctions among series sharing location/parameter/statistic. No verified precedence rule was found that authorizes selecting an ID, newest record, parented record or unnamed sublocation. Keep the unresolved quality state unless the product supports distinct series/sublocations or a reviewed station-specific policy is established.

No historical observation data was fetched during this research. Captures are in `.omc/research/usgs-primary-series-metadata-2026-11-03.md` and the two `usgs-series-item-*-2026-11-03.json` files. A follow-up retry policy can fingerprint unresolved series sets and metadata, record successful full-recheck time separately from publication time, and defer unchanged ambiguity without declaring complete data. It must also address the observed publication cost separately; metadata retry controls alone do not optimize writing.
