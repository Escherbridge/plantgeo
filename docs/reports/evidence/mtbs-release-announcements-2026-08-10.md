# MTBS annual release publication dates — evidence record

- **Intent:** establish defensible completion dates for MTBS fire years 2019, 2023 and 2024, to
  extend `MTBS_ANNUAL_RELEASE_DATES` in `services/agri-data-service/src/agri_data_service/ingest/mtbs.py`.
- **Captured:** 2026-08-10
- **Primary sources:**
  - https://www.mtbs.gov/announcements (and `?page=1`, `?page=2`, `?page=3`)
  - https://www.mtbs.gov/data-availability
  - https://www.sciencebase.gov/catalog/item/5e541969e4b0ff554f753113 (DOI 10.5066/P9IED7RZ) — **HTTP 403 on direct fetch**, only indirect confirmation obtained ("ver. 12.0, April 2025", coverage 1984–2024)

The rule this table encodes: a fire year's honest `data_available_at` is the publication date of the
LAST quarterly release that added fires from that year — confirmed by the NEXT release having moved
on to later fire years. Deliberately late; under-claiming knowledge is safe, leaking hindsight is not.

## Verified release chronology

| Date | Fire year(s) added | Count | Note |
|---|---|---|---|
| 2018-08-03 | 2016 (complete) | 1,337; total 21,673 | |
| 2019-08-29 | 2017 (complete) | 1,296; total 22,969 | "contains all 2017 fires" |
| 2020-04-02 | 2018 (announcement of intent) | — | |
| 2020-07-23 | 2018 (interim, 15 western states + AK) | 413 | |
| 2020-11-24 | 2018 (**remaining**) | 716; total 1,129 | 413+716=1,129 exact — *existing entry* |
| 2020-12-16 | — | — | Data Explorer tool launch |
| 2021-04-21 | 2019 | 353 | record spans 1984–2019, 26,557 records |
| **2021-09-27** | **2019 (remaining)** | **457; total 810** | 353+457=810 exact; project total 28,584 — **NEW ENTRY** |
| 2022-02-15 | 2020 (interim) | 417; total 28,982 | **no mention of 2019** — the transition signal |
| 2022-04-28 | 2020 (remaining) | 397; total 814 | *existing entry* |
| 2022-08-10 | 2021 | 154; total 29,533 | |
| 2023-01-11 | 2021 | 393 | |
| 2023-04-07 | 2021 | 222; total 30,148 | |
| 2023-08-09 | 2021 (remaining) | 257; total 1,026 | *existing entry* |
| 2023-10-26 | 2022 | 209; total 30,627 | |
| 2024-01-24 | 2022 | 297 | |
| 2024-05-01 | 2022 | 325 | |
| 2024-08-22 | 2022 (remaining) | 348; total 1,179 | *existing entry* |
| 2024-10-31 | 2023 + 2024 | 449 (53 from 2023) | cohort 2022 closed, 2023/2024 opened |
| 2025-01-31 | 2023 + 2024 | 299 (106 from 2023) | |
| 2025-04-28 | 2023 + 2024 | 399 (295 map products 2023; 104 **Initial Assessments** 2024) | |
| 2026-04-07 | 2023 + 2024 + 2025 | 645 (482 / 147 / 16) | 482 new 2023 fires — cohort plainly still open |
| 2026-05-27 | 2023 + 2024 + 2025 | 207 (98 / 104 / 5) | |
| 2026-07-01 | reissue 2023/2024/2025 | — | data-migration fix, not new fires |
| 2026-07-15 | "multiple fire seasons" | 315 | year breakdown not exposed |

## Verdicts

### 2019 → `date(2021, 9, 27)` — ADOPTED, high confidence

> "MTBS has released the remaining 457 fire mappings for 2019, bringing the total release for 2019
> fires to 810."
> — https://mtbs.gov/articles/announcement/mtbs-data-release-september-27-2021

Same "remaining" completion wording as the 2018 entry, plus exact arithmetic (353+457=810) and a
confirmed transition: the 15 February 2022 release names only fire year 2020.

Caveat matching the existing 2018 entry: no independent live national perimeter count for 2019 was
obtainable externally, so the announced 810 is uncorroborated by that particular cross-check.

### 2023 → NO DEFENSIBLE DATE, high confidence

No release has ever used completion language for 2023, and 482 *new* 2023 fires arrived as recently
as 2026-04-07. MTBS states outright:

> "Currently, the MTBS team is mapping fires from 2023, 2024 and 2025 fire season..."
> "The team is planning to have 2023 and 2024 fires completed by the end of FY2026."
> — https://www.mtbs.gov/data-availability

FY2026 ends 2026-09-30 — a future target, not a completion. Revisit after that date.

### 2024 → NO DEFENSIBLE DATE, high confidence

Same admission, and less mature than 2023: the 2025-04-28 release delivered 2024 fires as
"Initial Assessments", MTBS's explicitly preliminary product tier, with 147 more arriving 2026-04-07.

## Corroboration of the four pre-existing entries

No contradictions. Every announced cumulative total reproduces exactly from its quarterly parts:
2018 413+716=1,129 · 2020 417+397=814 · 2021 154+393+222+257=1,026 · 2022 209+297+325+348=1,179.
