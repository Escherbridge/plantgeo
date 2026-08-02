---
type: implementation-plan
---

# Mycelium cloud-seeding spike plan

1. User selects the first spike from the specification's risk-ordered table
   (recommended: 001 dose-vs-background, the kill criterion). No spike executes
   without that selection.
2. For the selected spike, create a dated working directory under
   `conductor/tracks/mycelium_cloud_seeding_spike_20260802/spikes/NNN-name/`
   with a README stating the Given/When/Then question and approach.
3. Execute with desk research and disposable computation only; record every
   assumption and citation alongside the artifact.
4. Close the spike README with a VALIDATED / PARTIAL / INVALIDATED verdict,
   surprises, and a recommendation for whether the product concept survives.
5. Report the verdict to the user; the user decides the next spike or track
   closure. Update this track's status in `conductor/tracks.md` and
   `metadata.json` together.
6. If 001 or 002 returns INVALIDATED, record the kill and move the track to
   `complete` (concept killed) rather than salvaging around the result.

No step authorizes organism acquisition or culture, field release,
weather-modification or precipitation-efficacy claims, product commitments, or
Railway/production mutation.
