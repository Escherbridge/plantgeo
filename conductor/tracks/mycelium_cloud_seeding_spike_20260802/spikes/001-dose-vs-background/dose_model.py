#!/usr/bin/env python3
"""Spike 001: dose-vs-background for natural-transport INA-spore products.
Question: can a plausible amended area deliver enough active ice nuclei (IN)
to matter in a seedable cloud volume, compared to (a) the dose conventional
glaciogenic seeding delivers and (b) natural background INP?
All parameters are log-uniform Monte Carlo draws over deliberately generous
ranges; every assumption is printed. Stdlib only."""
import random, math
random.seed(42)
N = 20000

def logu(a, b):
    return 10 ** random.uniform(math.log10(a), math.log10(b))

# ---- per-hectare emission chain (seasonal) ----
# application rate (t/ha) * inoculum (spores/g) * seasonal sporulation multiplier
# = seasonal spore emission per ha. Multiplier accounts for mycelial growth and
# repeated sporulation; capped implicitly by the chosen ranges.
rows = []
for _ in range(N):
    app   = logu(0.5, 5.0)      # t/ha amendment        (typical ag amendment)
    inoc  = logu(1e6, 1e9)      # spores/g inoculum     (spawn-quality range)
    mult  = logu(10, 1000)      # seasonal emission multiplier (growth+sporulation)
    fIN   = logu(1e-4, 1e-1)    # fraction of spores active as IN at <= -10 C
    event = logu(0.05, 0.30)    # fraction of seasonal emission in seedable windows
    loft  = logu(1e-3, 0.30)    # fraction reaching cloud base over the corridor
    area  = logu(2e8, 1e9)      # target area m2 (200-1000 km2)
    depth = logu(300, 800)      # seedable cloud layer depth m
    conc  = logu(1e3, 1e4)      # required IN/m3 in layer (1-10 per liter)

    emission_ha = app * 1e6 * inoc * mult          # spores/ha/season
    delivered   = emission_ha * fIN * event * loft # active IN delivered per ha
    required    = area * depth * conc              # active IN needed in volume
    rows.append(required / delivered)              # hectares needed

rows.sort()
def pct(p): return rows[min(N - 1, int(p * N))]

print("MONTE CARLO (n=%d): hectares of amended land needed to dose one" % N)
print("seedable cloud volume (200-1000 km2 x 300-800 m) at 1-10 IN/liter")
for p in (0.10, 0.25, 0.50, 0.75, 0.90):
    print("  P%02d: %12.0f ha" % (p * 100, pct(p)))
for t in (1e2, 1e3, 1e4, 1e5, 1e6):
    print("  P(need < %8.0f ha): %.3f" % (t, sum(1 for r in rows if r < t) / N))

print()
print("REFERENCE: same cloud volume dosed with AgI (1e12-1e13 active IN/g at -10 C)")
for conc in (1e3, 1e4):
    req = 5e8 * 500 * conc
    lo, hi = req / 1e13, req / 1e12
    print("  500 km2 x 500 m @ %5.0f IN/m3: need %.2e IN = %6.1f-%6.1f g AgI"
          % (conc, req, lo, hi))

print()
print("SCENARIOS (point estimates, 500 km2 x 500 m @ 3 IN/liter = 7.5e14 IN):")
REQ = 5e8 * 500 * 3e3
scen = {
  "pessimistic": dict(app=0.5, inoc=1e6, mult=30,  fIN=1e-3, event=0.1,  loft=1e-3),
  "central":     dict(app=2.0, inoc=1e8, mult=300, fIN=1e-2, event=0.15, loft=3e-2),
  "optimistic":  dict(app=5.0, inoc=1e9, mult=1000,fIN=1e-1, event=0.3,  loft=0.2),
}
for name, s in scen.items():
    em  = s["app"] * 1e6 * s["inoc"] * s["mult"]
    dl  = em * s["fIN"] * s["event"] * s["loft"]
    print("  %-11s emission=%.1e spores/ha/season  delivered=%.1e IN/ha  ->  need %.0f ha"
          % (name, em, dl, REQ / dl))

print()
print("BACKGROUND: natural INP at -10 C ~ 1e-4 to 1e-2 per liter (DeMott 2010 range);")
print("seeding targets 1-10 per liter, i.e. a 100-10000x local uplift. There is no")
print("dose low enough to be 'undetectable against background' yet effective.")
