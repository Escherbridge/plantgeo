# Layer L1: Warehouse

## Responsibility
Database connection management, session factories, declarative SQL object loading, ORM mappings (`db/` and `models/`).

## Dependency Rules
- **May import**: `foundation` (L0).
- **May NOT import**: `method`, `pipeline`, `planes`, `interface`.


## MTBS completed-cohort publication contract

`mtbs_releases.py` owns the immutable completed-cohort date mapping shared by ingestion and
release serving. `ingest.mtbs` imports and retains its existing public symbol. This mechanical
extraction changes no dates or producer behavior. These are not all MTBS quarterly releases and
not a claim of completed recent fire seasons. A future partial-capture product needs explicit
publication semantics before it can extend serving eligibility.

Historical date evidence from the original ingestion declaration is retained below. These are
original cohort-completion observations, not a fresh upstream census. The original rationale about
hindsight does not prove that mutable EDW geometries fetched today existed on those old dates;
revised/partial products require independently dated capture evidence. Serving eligibility alone
cannot solve that provenance limitation:

Fire year -> the publication date of the release by which that year's mapping was complete.

MTBS does NOT publish one release per fire year. It publishes QUARTERLY (the program states
"early February, May, August and November", https://www.mtbs.gov/data-availability), and a single
fire year accretes across several quarterly releases spanning two to four calendar years. The only
honest single date for a cohort is therefore the date of the LAST release that added fires from
that year -- late by construction, which under-claims knowledge but can never leak hindsight.

A year still being mapped has no such date and MUST raise. Every entry is a dated release
announcement from https://www.mtbs.gov/announcements, cross-checked against the USGS ScienceBase
revision history for DOI 10.5066/P9IED7RZ
(https://www.sciencebase.gov/catalog/item/5e541969e4b0ff554f753113).
MTBS Data Release, 24 November 2020: released "the remaining 716 fire mappings for 2018,
bringing the total release for 2018 fires to 1,129". The word "remaining" is an explicit
completion statement -- the strongest evidence in this table.
MTBS Data Release, 27 September 2021: released "the remaining 457 fire mappings for 2019,
bringing the total release for 2019 fires to 810" -- the same explicit "remaining" completion
wording that makes 2018 the strongest entry in this table. 353 (21 April 2021) + 457 = 810
matches the announced cumulative total exactly. The next release, the "2020 Interim Data
Release" of 15 February 2022, names only fire year 2020, and no release through 15 July 2026
reopens 2019.
"2020 Data Release", 28 April 2022, added 397 fires and closed the cohort opened by the
"2020 Interim Data Release" of 15 February 2022 (417 fires). The next release, 10 August
2022, had moved on to fire year 2021. 417 + 397 = 814 equals the live national perimeter
count for 2020 exactly.
Last of four 2021 quarterly releases: 10 August 2022 (154), 11 January 2023 (393),
7 April 2023 (222) and 9 August 2023 (257). The next release, 26 October 2023, had moved
on to fire year 2022. The announced total of 1,026 is within six of the live national
perimeter count of 1,020.
Last of four 2022 quarterly releases: 26 October 2023 (209), 24 January 2024 (297),
1 May 2024 (325) and 22 August 2024 (348). The next release, 31 October 2024, had moved on
to fire years 2023 and 2024. The announced total of 1,179 is within two of the live
national perimeter count of 1,177. Corroborated by ScienceBase revision 9.0, 22 August 2024.

## Current MTBS snapshot descriptor

`mtbs_snapshots.py` validates the fixed 2018–2026 regional query contract, exact source manifest digest, bounded response inventory and D+1 UTC availability. Current capture coverage does not certify complete mapping of 2023–2026 fire seasons. The ownership floor is 2026-09-11; the five historical completed-cohort dates remain unchanged. `source_content_sha256` belongs to source evidence and repeat-capture detection, not the public descriptor.
