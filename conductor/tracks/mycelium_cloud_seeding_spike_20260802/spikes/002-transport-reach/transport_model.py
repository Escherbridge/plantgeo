#!/usr/bin/env python3
"""Spike 002: natural-transport-reach.
Question: what fraction of surface-emitted spores survives horizontal transport
and reaches cloud base downwind? Screening-level physics, stdlib only.
Mechanisms: Stokes settling + turbulent dry deposition in a mixed layer,
below-cloud wet scavenging, convective delivery fraction."""
import math

RHO, G, MU = 1100.0, 9.81, 1.8e-5   # spore density kg/m3, gravity, air viscosity

def v_settle(d_um):
    d = d_um * 1e-6
    cc = 1.0 + 2.52e-7 / d           # rough slip correction
    return RHO * G * d * d / (18 * MU) * cc

def survival(x_km, u, H, vdep):
    return math.exp(-vdep * x_km * 1000 / (H * u))

print("SETTLING VELOCITIES (unit-density spores):")
for d in (1, 3, 5, 8, 10, 15, 20):
    print("  d=%4.1f um: v=%8.5f m/s (%6.3f cm/s)" % (d, v_settle(d), v_settle(d) * 100))

print()
print("DRY TRANSPORT SURVIVAL fraction, mixed layer H=1500 m, vdep=max(v_settle,1.5e-3):")
print("  wind u (m/s) |  10 km   50 km   100 km  200 km")
for d in (3, 5, 10):
    for u in (3, 7, 15):
        vdep = max(v_settle(d), 1.5e-3)
        s = [survival(x, u, 1500, vdep) for x in (10, 50, 100, 200)]
        print("  d=%4.1f u=%2d | %6.3f  %6.3f  %6.3f  %6.3f" % (d, u, *s))

print()
print("WET SCAVENGING survival after 30 min rain (below-cloud, greenfield-gap regime):")
# below-cloud collection efficiency is LOW for 1-10 um (scavenging minimum);
# use Lambda = 1e-5 .. 1e-4 /s
for d, lam in ((3, 1e-5), (5, 2e-5), (10, 8e-5), (20, 3e-4)):
    print("  d=%4.1f um, Lambda=%.0e/s: survival=%.3f" % (d, lam, math.exp(-lam * 1800)))

print()
print("CLOUD-BASE DELIVERY: delivered = survival(50 km, u=7) x convective fraction")
for d in (3, 5, 10):
    s = survival(50, 7, 1500, max(v_settle(d), 1.5e-3))
    for fc in (0.01, 0.1, 0.3):
        print("  d=%4.1f: survival=%.3f x conv=%.2f -> delivered=%.4f" % (d, s, fc, s * fc))
print()
print("NOTE: horizontal survival of 3-10 um spores over 50-200 km is HIGH; the")
print("uncertainty is the convective fraction (0.01-0.3), not transport loss.")
