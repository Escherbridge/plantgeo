# Baseline: current retrieval code against the expanded eval sets (2026-09-26)

Recorded with `uv run strategy-kb eval --golden eval/<file>.json` against the local cached index, before any
R2 retrieval change lands. Full per-query detail lives in each `strategy-kb eval` run's own JSON output
(not committed — regenerate with the command above); this file keeps only the summary a later run diffs
against. Confirm `corpus_version`/`index_is_stale` with `strategy-kb status` before comparing a later run.

| file | kind | queries | hit@5 | MRR | forbidden@3 violation rate |
|---|---|---|---|---|---|
| `golden_queries.json` | golden | 24 | 1.0000 | 0.8326 | 0.00 |
| `paraphrases.json` | paraphrase | 72 | 0.9306 | 0.7393 | 0.00 |
| `lay_language.json` | lay | 8 | 0.7500 | 0.7500 | 0.00 |
| `ph_contrast.json` | contrast | 4 | 1.0000 | 0.8750 | **0.75** |
| `family_coverage.json` | family | 18 | 1.0000 | 0.9306 | 0.00 |
| `findings_coverage.json` | findings | 6 | 1.0000 | 1.0000 | 0.00 |
| `passages_coverage.json` | passages | 5 | 1.0000 | 1.0000 | 0.00 |
| `agent_queries_heldout.json` (HELD OUT) | agent | 10 | 1.0000 | 0.7083 | **1.00** |

The original golden set is confirmed saturated (hit@5 = 1.0, matches STRATEGIES.md); paraphrasing alone drops
MRR from 0.83 to 0.74 and hit@5 to 0.93, so the expanded sets do what the golden set could not: show room to
improve.

## What the expansion already found

- **The lime/sulfur pH trap is real and it is the dominant failure mode.** 3 of 4 `ph_contrast` pairs rank the
  *wrong-direction* strategy inside the top 3: "raise the pH" surfaces `elemental-sulfur-soil-acidification`
  first; "soil is too acidic, need to sweeten it" surfaces it second. Both held-out real-agent phrasings of
  the same need ("fix soil chemistry", "fix acidic sour pasture soil low pH") reproduce it too — this is not
  an artifact of how the contrast pairs are worded.
- **"sour ground" without the word "pH" misses lime entirely**: `lay_language`'s "sour ground, nothing will
  grow" returns no soil-chemistry-correction strategy at all in the top 5 (not even the wrong one). Vocabulary
  expansion needs to cover "sour" as a lay synonym feeding the pH-direction boost, not just fix pH direction
  once "pH" is already in the query.
- **"hardpan" also misses its family**: `lay_language`'s hardpan query returns zero compaction-relief
  strategies (`brassica-cover-cropping-compaction-relief`, `strip-tillage-zone-till`) in the top 5.
- The 18 previously-untested families all score hit@5 = 1.0 here, singly queried with their own vocabulary —
  useful as a floor, but it does not mean they retrieve well in a compound, realistic query; the paraphrase
  set is the more predictive signal for that (their per-family hit rate already sits at 0.67-0.83 for several
  families under paraphrasing: `mulch-and-residue-cover`, `manure-and-digestate-nutrients`,
  `post-fire-mulching`, `post-fire-erosion-barriers`).

Re-run all eight files after each retrieval change; a forbidden@3 rate that drops on `ph_contrast` but stays
high on `agent_queries_heldout` means the fix tuned the contrast pairs' exact wording rather than the
underlying pH-direction signal.
