# 002: natural-transport-reach

## Question

Given amended soil on real terrain, when wind erosion, splash, and convection
act, what fraction of emitted spores reaches cloud-base altitudes downwind
during seedable conditions?

## Approach

Screening-level physics (`transport_model.py`, results in `results.txt`):
Stokes settling with slip correction for 1-20 um spores; dry-deposition
survival in a 1.5 km mixed layer over 10-200 km; below-cloud wet scavenging in
rain; convective cloud-base delivery fraction as a parameterized multiplier.

## Key results

- Settling: 3-5 um spores fall at 0.03-0.09 cm/s — effectively suspended in
  any turbulent boundary layer.
- Dry transport survival to 200 km: 0.86-0.99 across wind speeds for 3-10 um.
- Wet scavenging (30 min rain): 0.87-0.98 survival for 3-10 um (the
  below-cloud scavenging minimum sits in our favor; >20 um particles scrub).
- Delivered fraction is dominated by the convective fraction (0.01-0.3), which
  multiplies directly into dose — this is folded into spike 001's `loft`.

## Verdict: VALIDATED

### What worked
- Natural transport carries 3-10 um spores tens to hundreds of km with minimal
  loss. Rain both triggers emission and scrubs — but scrubbing is weak exactly
  in the 3-10 um spore band.

### What didn't
- Nothing about horizontal transport kills the concept. The genuine unknown is
  what fraction of boundary-layer air (and spores) actually enters cloud
  updrafts during seedable conditions (0.01-0.3 range used).

### Surprises
- The rain-release vs. rain-scrubbing tension is much less severe than feared:
  the scavenging Greenfield gap protects exactly the particle sizes fungi emit.

### Recommendation for the real build
Do not spend effort on transport engineering. Spend it on emission dose
(spike 001) and on measuring the convective fraction empirically if this ever
moves to a field pilot.
