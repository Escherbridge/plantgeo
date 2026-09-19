# method/kernels (L1)

Numeric cores that phase 3 re-implements in Mojo: weighted-L2 neighbour search with an
exclusion mask, a bit-exact PCG64 plus seasonal bootstrap, and the cyclical/photoperiod feature
kernel. `PLANTGEO_ML_KERNELS=python|mojo` selects the implementation; `python` is the default
and the rollback, and `mojo` refuses to start when the built extension is absent.

Import rules: `foundation` ONLY, stricter than the rest of `method`, because a kernel has to be
callable from a Mojo boundary that carries no first-party context. A Mojo function imported from
Python takes at most six arguments, so a kernel passes one bundle rather than many scalars.

Empty until phase 3 (plan.md, Phase 3).
