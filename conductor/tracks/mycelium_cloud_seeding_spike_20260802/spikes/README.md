# Mycelium cloud-seeding spikes - index and track verdicts

Executed 2026-08-02 under `../plan.md` step 2-4. All work is desk research and
stdlib-only computation; nothing here touched the application, databases, or
Railway. Each spike directory contains its README (question, approach,
verdict) and, for computational spikes, the script and `results.txt`.

| # | Spike | Verdict | One-line result |
|---|-------|---------|-----------------|
| 001 | dose-vs-background | PARTIAL | Median ~20k ha to dose one cloud volume; pilot-scale fails in ~4 of 5 parameter draws; regional scale (10^4-10^5 ha) works in ~2 of 3. AgI does the same job in grams. |
| 002 | natural-transport-reach | VALIDATED | 3-10 um spores survive 50-200 km transport at 0.86-0.99, rain scrubbing weak in that band; convective fraction (0.01-0.3) is the real unknown, folded into 001. |
| 003 | attribution-design | PARTIAL | Precip-effect statistics: seasonal designs hopeless, event-level needs ~80 storm pairs for >=15% effects; <10% effects undetectable. Presence attribution via DNA tracer is cheap and feasible. |
| 004 | strain-candidate-screen | VALIDATED | Viable path: M. alpina archetype + cell-free IN protein option; pathogens/allergens killed by rule (Fusarium, Isaria, Cladosporium, smuts all excluded). |
| 005 | regulatory-classification | VALIDATED | Clean perimeter: agronomic claims + state ag-product law; the word "rain" in marketing collapses it into weather-modification regimes (15 USC 9A, state permits, tort exposure). |
| 006 | plantgeo-siting-engine | NOT RUN | Low risk; deferred pending user decision (it is a platform feature spec, not a feasibility gate). |

## Track-level conclusion

The concept SURVIVES in reframed form and DIES in its original form:

- Dead: products as an event-scale cloud-seeding tool. Chemistry/protein
  powders win per-gram by ~4-6 orders of magnitude (001), and the precip
  effect of a dilute product could never be proven anyway (003).
- Alive: products as agronomic goods that produce a measurable, regional,
  multi-season enrichment of warm-temperature INP — the Morris et al. (2014)
  bioprecipitation lever pulled deliberately, sold on soil health, with
  delivered-dose (not rainfall) as the measurable atmospheric claim (001/003),
  inside a mappable legal perimeter (005), with a screened candidate
  archetype (004) and no transport bottleneck (002).

## Recommended next step

User decision: (a) close track as complete (exploratory question answered),
(b) run 006 to spec the PlantGeo siting/monitoring wedge, or (c) commission
the two desk items flagged in 005 (M. alpina AAFCO/GRAS status; 50-state
weather-mod survey) if the product concept advances.
