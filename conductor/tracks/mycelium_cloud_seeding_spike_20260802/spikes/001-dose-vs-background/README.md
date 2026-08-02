# 001: dose-vs-background

## Question

Given plausible amendment application rates and sporulation yields, when lofted
by natural pathways, does deployed INP flux exceed natural background by a
detectable/effective margin in a target corridor?

## Approach

Monte Carlo (n=20,000, log-uniform parameters, `dose_model.py`, results in
`results.txt`). Emission chain: application rate x inoculum density x seasonal
sporulation multiplier -> active-IN fraction x seedable-window fraction x
loft fraction -> delivered IN/ha, compared against the IN required to dose a
seedable cloud volume (200-1000 km2 x 300-800 m) at 1-10 IN/liter (standard
glaciogenic seeding target).

## Key results

- Hectares needed per seedable cloud volume: median ~20,000 ha; P25 ~1,500 ha;
  only 21% of parameter draws succeed below 1,000 ha; 66% below 100,000 ha.
- Reference: the same cloud volume needs only 25-2,500 g of AgI
  (1e12-1e13 active IN/g at -10 C). Fungal product cannot compete per-gram
  with chemistry for deliberate event seeding; biology's advantage is that the
  factory self-replicates in situ for free.
- Natural background INP at -10 C is ~1e-4 to 1e-2 per liter; effective
  seeding needs 1-10 per liter. No low dose is simultaneously effective and
  hidden in background.

## Verdict: PARTIAL

### What worked
- The emission chain closes arithmetically; nothing in the physics forbids it.
- At regional adoption scale (10^4-10^5 ha), a majority of parameter draws
  deliver a seeding-relevant dose. Transport (spike 002) is not the limiter.

### What didn't
- Pilot-scale deployment (<1,000 ha) credibly doses a cloud volume in only
  ~1 in 5 parameter draws. Event-scale "cloud seeding with products" is not
  defensible as the primary claim.

### Surprises
- The AgI mass comparison (grams vs. hectares) is far starker than expected.
  Any deliberate, timed seeding operation will use AgI or protein powders
  (cf. Snomax); living product is a dilute but persistent background source.

### Recommendation for the real build
Reframe the product: NOT an event-scale cloud-seeding tool, but a regional,
multi-season INP-enrichment land-management play sold on agronomic value.
The measurable near-term claim should be delivered-dose detection (ridge-line
INP samplers + DNA barcoding), which requires orders of magnitude less
material than a precipitation effect (see 003).
