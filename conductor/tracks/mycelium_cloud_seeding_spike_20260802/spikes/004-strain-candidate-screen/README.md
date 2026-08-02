# 004: strain-candidate-screen

## Question

Given published INA literature, when screening for warm onset (>= -6 C),
active fraction, safety, and culturability, does a viable candidate list with
named lab test methods result? Pathogens and known allergens excluded by rule.

## Approach

Desk synthesis of the track's literature base (see `../spec.md` References)
plus standard mycology/aerosol assay practice. No organisms handled.

## Candidate table

| Tier | Candidate | IN onset | Safety | Culturability | Notes |
|------|-----------|----------|--------|---------------|-------|
| 1 | Mortierella alpina | -5 to -6 C (all INA isolates) | Ubiquitous soil saprobe; industrial food-oil production history | Solved at industrial scale | Archetype payload; proteinaceous IN; spores already in air/rain |
| 1 | Cell-free IN protein powder (donor species per VT 2026 work) | high subzero (protein-level) | No live organism released | Fermentation + purification | Biggest regulatory simplification for P1/P2; not viable for P3 |
| 2 | Other Mortierellales clades (4 known, soil-type affiliated) | likely similar | soil saprobes | culturable | Diversify for soil-type matching |
| Watch | Lichen mycobionts (e.g., Xanthoria) | warm IN reported | benign | slow/difficult culture | Watchlist only |
| EXCLUDED | Fusarium spp. (IN-active) | warm | PLANT PATHOGEN - rule kill | - | - |
| EXCLUDED | Isaria farinosa (IN-active) | warm | entomopathogen - rule kill | - | - |
| EXCLUDED | Cladosporium spp. | poor IN + major allergen | rule kill | - | - |
| EXCLUDED | Smuts/rusts (best IN in Haga 2014 set) | warm-ish | crop pathogens - rule kill | - | - |
| EXCLUDED | Pseudomonas syringae (bacterial benchmark) | -2 to -5 C | plant pathogen; Snomax uses killed cells | - | precedent only |

## Kill rules (apply before any other merit)

1. Any plant, insect, or vertebrate pathogenicity -> exclude.
2. Any known allergen (WHO/IUIS allergen database homology) -> exclude.
3. Any toxigenic genus -> exclude.
4. Non-native to deployment region where a native functional equivalent
   exists -> prefer native.

## Lab test methods (when a wet-lab phase is ever authorized)

- Immersion droplet freezing array: T50 and cumulative IN per gram dry mass
  at -6 / -8 / -10 / -12 C (the decision temperatures).
- Spore aerodynamic sizing (APS); target 3-10 um.
- UV-B and desiccation survival (atmospheric persistence proxy).
- P3 only: in-vitro rumen fermentation (48 h) + simulated gastric passage,
  then germination assay on dung substrate (coprophilous competence).
- Soil microcosm persistence and spread assay (invasiveness screen).
- WGS + ITS barcoding (identity + the DNA tracer needed by spike 003).

## Verdict: VALIDATED

A viable, rule-screened candidate path exists: M. alpina as living-product
archetype and cell-free IN protein as the low-regulatory-risk form. The
screen's exclusions are cheap to apply at desk stage and were applied above.
No strain work is authorized by this verdict.
