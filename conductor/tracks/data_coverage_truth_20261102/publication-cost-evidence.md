---
type: validation-report
---
# Availability publication cost and batching boundary

An isolated exploratory probe used the real public availability publisher, real fixture Parquet/hash evidence and snapshot revalidation with in-memory CAS and a simulated exclusive barrier. Each case began with 366 published days and four rungs (1,464 rows). It made no network/provider calls and is not production validation. Artifacts: `.omc/research/publication-cost-probe.py` and `.omc/research/publication-cost-probe.json`.

| Changed days | Mode | Generation writes | Generation bytes | Total GETs | Data-proof GETs | Pointer/rollup CAS each |
|---:|---|---:|---:|---:|---:|---:|
| 1 | Per day | 1 | 208,446 | 34 | 8 | 1 |
| 1 | Batch | 1 | 208,446 | 34 | 8 | 1 |
| 10 | Per day | 10 | 2,083,820 | 340 | 80 | 10 |
| 10 | Batch | 1 | 208,348 | 286 | 80 | 1 |
| 100 | Per day | 100 | 20,840,561 | 3,400 | 800 | 100 |
| 100 | Batch | 1 | 208,405 | 2,806 | 800 | 1 |

Changed-day evidence checks are retained. At 100 days, generation reads fell from 200 to two and total bytes read from 43.46 MB to 1.83 MB. The probe excludes data writes, derivation, claim preparation, network latency, contention, actual database locks and concurrent readers. It does not attribute all 1,668 seconds of the observed production write phase to this overhead or establish a production speedup ratio.

## Do not defer arbitrary indexed-day rewrites

Source inspection found that physical day parts and completion markers use mutable fixed keys. The current availability generation authorizes their exact bytes; authorized serving verifies completion and part digests. Rewriting those bytes invalidates the old generation's authority until the new generation advances. Serving correctly refuses the mismatch. Pending claims are recovery records, not alternate serving pointers.

Delaying publication until 100 existing days are rewritten would enlarge the serving refusal window and leave more days awaiting recovery after a crash. The benchmark's grouping must not be applied directly to the active rewrite loop. Keep immediate indexing of existing-day corrections and repairs. Arbitrary batching without this tradeoff would require immutable/versioned physical objects and retention for in-flight readers.

The safest narrow batching seam is the existing bounded drain of already pending terminal claims (currently eight). A future change could validate them, publish their rows once under the existing barrier, and clear only the exact incorporated claims. Malformed claims must not starve valid ones; contention, mutation and crash leave claims recoverable; a crash after pointer CAS must allow exact replay. Truly unpublished appends could separately use small count/time-bounded groups without invalidating existing selected days. Neither optimization is implemented by this evidence report.
