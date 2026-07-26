---
type: review-packet
---

# PlantGeo evidence review packet

This packet starts an owner-and-consultant review of the public-data delivery
effort. It separates real evidence limitations from product decisions.

Read in this order:

1. [`blockers.md`](./blockers.md) — what stopped, why, and what resolves it.
2. [`warehouse-access.md`](./warehouse-access.md) — local database access and
   the retained data plane.
3. [`queries.sql`](./queries.sql) — copy-ready read-only inspection queries.
4. [`consulting-loop.md`](./consulting-loop.md) — research, alignment, and
   bounded-spike working rhythm.

No model, forecast, strategy, or causal claim was emitted. That preserves the
evidence so we can choose the smallest useful next product together.

## Evidence at a glance

| Evidence plane | What is sound | Current delivery decision |
| --- | --- | --- |
| GHISACONUS historical crop spectra | 6,988 rows, 131 bands, five crop labels, 99 source `Image` groups, checksum-bound in the warehouse | Five-class grouped classifier abstains: rice has only two independent images, not the minimum three partitions. |
| NASA POWER weather signal | 1,462 daily values and 14 seven-day forecast origins preserved in a checksummed frozen export | Time-honest backtest abstains: all source rows were recorded after every simulated origin. |
| Broader product exploration | Shared lineage, source, support, and process metadata contract is documented | Water, energy, vegetation, soil, yield/cost, biodiversity, and scenario models wait for separately retained target contracts. |

Use this packet to decide whether the next spike should redefine a target with
transparent wording, acquire independent evidence, or build an evidence
explorer before a predictive claim.
