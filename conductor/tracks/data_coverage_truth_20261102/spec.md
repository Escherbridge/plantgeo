---
type: feature-spec
---
# Lane A: coverage-driven ingestion and truthful serving

Owner request: 2026-11-02. Deploy data fixes independently of Catalyst work.

## Outcome
A US-region operator declares the needed historical period and coverage target, supplies provider keys, and starts bounded ingestion. Historical acquisition remains enabled but automatically idles when its target is met; missing coverage can reopen work. Forward capture and bounded upstream revision checks continue. Serving/UI/agent state must distinguish available, partial, stale, governed absent, not yet fetched and failed.

## Scope
- Typed config-driven archival coverage target with explicit start and fixed end or declared source-settled rolling edge.
- Region/support binding inherited from the selected region manifest; no global coverage inference.
- Full-serving-rung and source-support completeness required for data coverage. Governed absence shown separately; not silently counted as data.
- Bounded missing-period work; target reached means zero upstream history requests, not disabled scheduling.
- Lower-than-full targets must disclose unresolved periods; default full coverage.
- Forward and correction windows remain independent of historical target.
- Preserve historical weather access. Live November 2 probes prove 2018 historical-forecast rows exist; separate historical cohort provenance/repair ownership from current-only acquisition in a follow-up, not by shortening the history floor.
- Fix confirmed UI rendering/availability mismatches found in the live-contract/source audit.
- US-first setup guide with truthful source/key/region limitations and inspection commands.

## Exclusions
No Catalyst ingestion, forecast activation, intervention-effect certification, historical data deletion, global provider entitlement claims, or automatic legacy-to-config cutover. Legacy paths lacking this policy remain explicitly identified.

## Acceptance
1. Deterministic cases cover empty, partial, all-rung complete, absent, unknown, wrong-support and reopened states.
2. Targets cannot expand beyond configured source capabilities or hide incomplete spatial support.
3. The declared denominator does not derive from whatever data happens to exist.
4. Known-complete historical periods trigger no upstream refetch; recent correction windows continue.
5. UI errors/truncation/served dates reflect the reader actually displayed.
6. New operator can identify region, required keys, date range, target, run and status commands from one guide.
7. Two dev-drive review cycles and one integrated final sweep; deployment and live browser evidence recorded separately.
