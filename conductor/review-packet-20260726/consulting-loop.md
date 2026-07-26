---
type: working-agreement
---

# Consultant-style research and spike loop

## Working rhythm

We can work as short decision cycles rather than attempt a full product plan in
one pass:

1. **Frame:** choose one user goal, user-visible decision, acceptable scale,
   and prohibited claim.
2. **Research:** inspect only candidate sources and retained evidence for that
   target; return coverage, availability, terms note, and uncertainty.
3. **Alignment:** choose whether to accept the target, change its wording/scale,
   acquire more evidence, or abstain.
4. **Spike:** build the smallest replayable experiment: frozen input,
   data-quality report, baseline, one candidate, holdout, and result/abstention.
5. **Review:** compare against the agreed bar; promote only passed evidence and
   contracts, while retaining rejected paths as decision history.

## One goal at a time

| Future goal | First question | Minimum evidence before a model spike |
| --- | --- | --- |
| Water | What exact usage or availability outcome is forecast? | Retained target history, units, support, availability time, holdout windows |
| Energy | Pump energy, grid energy, or another outcome? | Meter/public aggregate target with mapping and cadence |
| Vegetation | Satellite index or field measurement? | Public non-sensitive AOI, scenes, QA, availability calendar, aggregation recipe |
| Soil | Which depth/property is meaningful? | Sensor or reanalysis target with native support and honest wording |
| Yield/cost | County-scale or site-scale decision? | Authoritative history, revision policy, units, geography alignment |
| Biodiversity | Which observable indicator? | Repeated records, detection/coverage limits, temporal holdout |
| Scenario | Which assumptions are user-controlled? | Explicit parameters and deterministic recipe; no implied efficacy |

## Immediate candidate spikes

1. **Crop decision spike:** choose a four-class grouped historical classifier or
   an evidence explorer; do not hide rice.
2. **Weather lineage spike:** capture a historically available source release
   per forecast origin, then reassess the seven-day backtest.
3. **Vegetation research spike:** identify a non-sensitive public AOI and assess
   whether an availability-aware Landsat anomaly target is viable.

Each spike ends in an owner check-in: continue, revise, or stop. No spike becomes
a production service, strategy selector, or causal claim without a separate
decision.
