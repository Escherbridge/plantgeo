---
type: specification
---

# Restoration Agriculture predictive-demo governance

## Goal

Build a demo that lets a user select a clearly defined operational or ecological
goal and inspect a goal-specific prediction, its uncertainty, coverage, drivers,
and evidence gaps. The initial scope follows Restoration Agriculture
Development's public themes—water management, perennial/agroforestry systems,
earthworks, and tree planting—without presenting its public materials as a data
source or an efficacy claim.

## Permitted demo targets

Each target is independently defined, trained, validated, and displayed:

- future water demand or seasonally adjusted water-use anomaly;
- pump/irrigation energy forecast after a verified meter-to-zone link;
- vegetation-condition forecast or anomaly from appropriately scaled imagery;
- observed soil-moisture forecast where a site sensor exists; and
- yield, cost, or biodiversity forecast only after an authoritative historical
  record is supplied.

## Current-phase data decision

The current phase uses only retained warehouse signals and accessible public
datasets. Utility, parcel, private-AOI, meter-to-zone, and private operational
records are deferred enhancements, not acquisition tasks or blockers for this
phase. The initial capabilities are:

- historical crop-spectrum classification from the accepted GHISACONUS public
  benchmark; and
- retrospective regional meteorological-signal backtests from the retained
  NASA POWER warehouse at its declared native support.

These are separate models with separate targets and evidence cards. They may
share lineage and UI primitives, but their rows must not be joined to invent a
property, water-demand, yield, restoration, or strategy-effect target.

## Non-goals and invariants

- A forecast, association, scenario card, or public dataset cannot be called a
  strategy recommendation, intervention effect, or causal efficacy result.
- Forecast residuals, satellite indices, public ET estimates, and utility usage
  are not intervention/control outcome labels.
- Every input records source/release, version, licence/terms note, checksum,
  native support, observed/valid time, recorded-availability time, quality
  state, and handling of gaps/corrections. Public non-synthetic Kaggle data may
  support an isolated evaluation benchmark with its listed source and lineage
  caveats preserved; it cannot silently become property, operational, or causal
  evidence.
- Utility data additionally requires documented acquisition authorization or
  consent, a purpose-limited pseudonymous meter-to-zone mapping, and boundary
  provenance/access controls. Do not expose exact sensitive service points,
  infrastructure, or private property geometry in the demo.
- A user-visible result must name its target, geography/scale, data coverage,
  forecast origin, uncertainty, and known evidence gaps.
- No Railway mutation, scheduled ingestion, forecast publication, or
  `effect_candidate` finalization is in scope.

## Graduation rule

Public data may support a map-first demo and environmental context. An
evaluation-only strategy comparison requires a governed mapping of property or
management-zone identity, dated intervention episodes, baseline and outcome
windows, assignment-time covariates, and eligible comparison units or periods.
It remains governed by
[`strategy_selection_governance_20260726`](../strategy_selection_governance_20260726/).
