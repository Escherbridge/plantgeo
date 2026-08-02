# 003: attribution-design

## Question

Given continuous, non-random product adoption, when monitoring downwind, can
any affordable design separate a precipitation effect from natural variability?

## Approach

Monte Carlo power analysis (`power_analysis.py`, results in `results.txt`).
Design A: seasonal paired-watershed statistics (crossover and before/after).
Design B: event-level randomization (SNOWIE-style storm pairs). Power at
alpha ~ 0.05 across realistic precip CV (0.25-0.6), inter-basin correlation
(0.6-0.85), and effect sizes (5-20%).

## Key results

- Design A is hopeless: power 0.07-0.52 even at 15-20 seasons. A
  before/after seasonal comparison can never certify a 5-15% effect on any
  commercially relevant timeline.
- Design B is better but still hard: ~80 randomized storm pairs for ~0.5
  power at a 15% effect; ~0.69 power at 20%. At 10-20 seedable storm pairs
  per season, that is 2-4+ seasons for effects >= 15%.
- Effects of 5-10% are effectively undetectable by precipitation statistics
  at any affordable sample size.

## Verdict: PARTIAL

### What worked
- Presence attribution is cheap and feasible: DNA-barcoded strains in
  downwind aerosol/snowpack samples prove the delivered dose with a few
  ridge-line samplers. This is the near-term measurable claim (and needs far
  less material than a precip effect — see 001 recommendation).
- Physical-chain evidence (radar reflectivity signature, INP filter counts at
  cloud base, DNA detection) strengthens attribution at far lower cost than
  precip-magnitude statistics; this is how SNOWIE actually made its claim.

### What didn't
- Any business model that requires proving a <10% precipitation uplift is
  dead: the statistics cannot deliver that proof on a commercial timeline.

### Surprises
- Even the "good" design (event randomization) only reaches acceptable power
  for effects >= 15-20%.

### Recommendation for the real build
Adopt a two-tier attribution standard: (1) delivered-dose proof via DNA
tracer + INP samplers (affordable, near-term); (2) precip-effect claims only
via multi-season randomized event-level trials, if ever. Monetization must
rest on agronomic value and measured emission inventories, never on proven
rain.
