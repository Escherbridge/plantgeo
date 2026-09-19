# method/kernels (L1)

Three numeric cores with two interchangeable implementations each: a pure-Python reference in
`reference.py` and a Mojo translation in `<service root>/kernels/*.mojo`, selected by
`PLANTGEO_ML_KERNELS`. Spec FR-9, plan phase 3.

| kernel | what it computes | who calls it |
|---|---|---|
| `neighbor_search` | weighted-L2 distance over a candidate archive, a two-sided exclusion mask and a stable top-k | `method/ml/analog_ensemble.find_analogs`, hence `pipeline/analog_ensemble_daily` |
| `seasonal_bootstrap` | an ensemble path per draw, clipped, then linear-interpolated quantiles | `method/monte_carlo/signal` (both estimators) and `method/monte_carlo/vegetation_ndvi_forecast`, hence `pipeline/monte_carlo_daily` |
| `seasonal_features` | cycle-closed day-of-year sine and cosine, FAO-56 photoperiod | `pipeline/fire_risk_plane.with_seasonality`, hence `pipeline/fire_risk_features` |

## Why it is shaped like this

**The reference is the definition.** `reference.py` is what the kernel MEANS; the Mojo file is a
translation judged against it by `tests/kernels/test_parity_*.py` and
`scripts/kernel_parity_receipt.py`. `scripts/regenerate_kernel_fixtures.py` therefore runs the
reference and never a Mojo build: regenerating a golden output from the thing under test would make
the assertion circular.

**Mojo is an optimisation and never a dependency.** `python` is the default and the rollback in
`config.Settings`, in the Docker image and in `kernels()`. Every phase after this one works with no
Mojo build at all. Conversely `mojo` REFUSES to start when the extensions are absent or were built
for another platform: a quiet downgrade would make a benchmark unfalsifiable and would let a
deployed service claim an optimisation it is not running.

**One bundle, not many scalars.** A Mojo function imported from Python takes at most six arguments,
so each kernel takes one typed bundle from `bundles.py` and `native.py` flattens it into contiguous
numpy buffers. The neighbour search packs its six integer scalars AND its per-candidate day index
into a single int64 array for the same reason. Output arrays are allocated by the caller, so
nothing allocates across the boundary.

**No RNG is ported.** The seasonal bootstrap consumes a draw stream numpy's seeded PCG64 already
produced. The seed stays the one authoritative reproduction handle, and the two implementations can
never disagree about a random number because neither of them draws one.

**`foundation` only.** The lattice test is stricter here than anywhere else in `method`, because a
kernel has to be callable across a boundary that carries no first-party context. The FAO-56
constants therefore exist twice, here and in `pipeline/fire_risk_plane.py`, and
`tests/kernels/test_parity_seasonal_features.py` asserts the kernel against that module's scalar
helpers rather than importing them. The rule `method/monte_carlo` may not import `method/ml` is
unchanged; what phase 3 relaxed is that both siblings may now import `method/kernels`, which sits
strictly below them and still cannot reach back.

## Three things that cost a debugging session

1. **Floating-point contraction.** Mojo defaults to Clang's `-ffp-contract=fast` and fuses
   `offset + scale * value` into an FMA, which rounds once where numpy rounds twice. Measured
   2026-09-19: distances off by up to 8.9e-16, bootstrap quantiles by 2.2e-16.
   `scripts/build_kernels.sh` passes `--fp-mode contract=off`, and that flag is a parity contract,
   not a tuning knob.
2. **numpy's `linear` quantile is not the textbook formula.** Its virtual index is `(n - 1) * q`,
   and numpy's own comment says it prefers that "to avoid some rounding issues" over the
   Hyndman and Fan form `n*q + (alpha + q*(1 - alpha - beta)) - 1` that `_compute_virtual_index`
   spells out. The two disagree in the last bit. Its interpolation also switches association at
   `gamma >= 0.5`: `a + (b - a) * gamma` below, `b - (b - a) * (1 - gamma)` at or above.
3. **The probability's last bit matters.** `0.1 * 100.0 / 100.0` is `0.10000000000000002`, not
   `0.1`, and `numpy.percentile` derives its probability that way. The call sites pass
   `numpy.true_divide(percentages, 100.0)` so the kernel sees exactly what `percentile` would have.

## What is bit-identical and what is not

The neighbour search and the seasonal bootstrap are asserted bit-identical: both are pure
float64 arithmetic in a fixed order. The seasonal features are not, because `sin`, `cos`, `tan` and
`acos` are libm on the Python side and Mojo stdlib here; sine and cosine came out bit-identical
anyway and the photoperiod within 2.5e-16 relative, so the harness pins the first two at 1e-12
absolute and the photoperiod at 1e-12 relative. One ulp of a 43,200 second day is already 7.3e-12
seconds, which is why the photoperiod cannot carry an absolute tolerance.

## What the bundle refuses, and what it deliberately does not

`bundles.py` rejects any NaN or infinity in every float64 ARRAY a kernel reads
(`KernelArgumentError`, built on `foundation.canonical.validate_finite` so the message names the
offending flat index). The reason is that a native kernel has no exception to raise across the
boundary and no NaN-aware comparison: a NaN distance is never less than the running best, so a
poisoned candidate is silently never selected, and an infinity is indistinguishable from the
`Float64.MAX` sentinel the search uses for "ineligible". Either would answer a plausible-looking
neighbour set computed from garbage.

The scalar `lower_bound` and `upper_bound` are deliberately exempt: `-inf`/`+inf` is how the
empirical-resample shape spells "do not clip", and it is a legitimate value there.

## Two declarations that must not drift

The search bundle's header size is written twice: `NEIGHBOR_SEARCH_HEADER_SLOTS` in `native.py`
and `HEADER_SLOTS` in `kernels/knn_search.mojo`. An integer cannot be shared across the boundary
at compile time, and drift is SILENT -- the kernel would read a day index as a scalar and answer
a wrong-but-plausible neighbour set. `NativeKernels.describe_neighbor_bundle` therefore echoes the
header back (the six scalars plus the first per-candidate day, read at the offset the Mojo file
believes the header ends at) and `tests/kernels/test_kernel_dispatch.py` compares all eight. The
echo is a diagnostic; no run path calls it.

## Where everything lives

- Mojo sources and their shared buffer helper: `<service root>/kernels/*.mojo`
- Mojo-side unit tests (Mojo 1.0 removed `mojo test`, so they are an executable):
  `<service root>/kernels/tests/run_tests.mojo`
- Benchmark: `<service root>/kernels/bench/bench_knn_search.py`
- Toolchain, pinned versions and the task list: `<service root>/pixi.toml`, and `RUNBOOK.md`
  section "Environment" for how to drive it from WSL2.
