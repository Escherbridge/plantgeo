# Phase 3 predictions and findings: Mojo kernels (slice `p3-mojo-kernels`)

Written 2026-09-19 in the worktree `C:\Users\atooz\AppData\Local\Temp\plantgeo-p3`, branch
`ml/p3-mojo-kernels` off `origin/main` at `96831d8b`. This slice is the declared exception to the
"authors never run gates" rule (owner 2026-08-25): the toolchain proof IS the deliverable, so every
gate below was actually run and its output is reported verbatim in summary.

## 1. Toolchain, measured

| piece | value |
|---|---|
| Mojo | **1.0.0 (ed45d567)**, pinned `mojo = "==1.0.0"` in `services/plantgeo-ml-service/pixi.toml` |
| conda channel | `https://conda.modular.com/max` (confirmed live; `mojo` resolves there), plus conda-forge |
| latest available | Mojo **1.1.0**, released 2026-09-10, deliberately NOT taken |
| conda package licence | `LicenseRef-Modular-Proprietary` in the package metadata, which is NOT the Apache-2.0 the research findings recorded for the open-sourced compiler. Worth a look before anyone calls the toolchain Apache-2.0 in a public artifact |
| pixi | 0.81.0, installed into `~/.pixi` in WSL2 Ubuntu |
| WSL2 kernel | `6.6.87.2-microsoft-standard-WSL2`, glibc 2.39, 8 logical processors |
| pixi env Python | CPython 3.12.14, numpy 2.5.3 |
| disk used | `.pixi/` in the worktree is **2.1 GB** for both environments (`default` ~913 MB alone). Gitignored. The WSL `~/.pixi` binary plus cache is a further few hundred MB |

## 2. What compiled, and what the documented API actually is

All three kernels compile and load. Six things in the FINDINGS notes and in the published
Python-from-Mojo guide did not match Mojo 1.0 as installed, and each cost a compile cycle:

1. **The stdlib is namespaced under `std`.** `from python import PythonObject` does not resolve;
   `from std.python import PythonObject` does. Same for `std.os`, `std.math`, `std.memory`,
   `std.python.bindings`. The public manual still shows the unprefixed form.
2. **`alias` is deprecated; the keyword is `comptime`.**
3. **`fn` has been removed.** Everything is `def`, and `def` no longer implies `raises`, so a
   function calling `Int(py=...)` must say `raises` explicitly.
4. **`UnsafePointer` is deprecated in favour of `Pointer`**, which is parameterised by an origin.
   A numpy buffer becomes `Pointer[Float64, MutUnsafeAnyOrigin](unsafe_from_address=Int(py=array.ctypes.data))`,
   and element access wants `buffer[unsafe_offset=index]`. `MutableAnyOrigin` does not exist.
5. **`mojo test` is gone.** There is no `test` subcommand in 1.0, so the Mojo-side unit tests are a
   plain executable (`kernels/tests/run_tests.mojo`) run by `pixi run test-kernels`; a failed
   expectation raises and exits non-zero.
6. **There is no `sort` in the stdlib this kernel could import** (`std.sort`, `std.algorithm.sort`
   and `List.sort()` all fail), so the bootstrap carries its own heapsort. Which sort it is cannot
   be observed: equal float64 values are indistinguishable.

The six-argument cap held: every Python-facing function takes exactly six or fewer arguments, with
the neighbour search packing its scalars and its per-candidate day index into one int64 bundle.

## 3. Parity: two bit-identical, one within tolerance

`conductor/tracks/plantgeo_ml_service_20260918/evidence/phase3-parity.json`, verdict **PASS**:

| kernel | requirement | measured |
|---|---|---|
| `knn_search` distances | bit-identical | identical, max deviation 0.0 |
| `knn_search` indices | identical order | identical |
| `seasonal_bootstrap` (scaled, clipped) | bit-identical | identical |
| `seasonal_bootstrap` (empirical resample) | bit-identical | identical |
| `seasonal_features` sine, cosine | 1e-12 absolute | bit-identical, 0.0 |
| `seasonal_features` photoperiod | 1e-12 relative | 1.46e-11 absolute, **2.52e-16 relative** |

**The photoperiod is the one place the brief's literal tolerance could not be met and should not
have been.** `atol=1e-12` on a quantity of ~43,200 seconds is tighter than one ulp (7.3e-12), so it
would demand bit-identical `acos`/`tan` across two libm implementations. It is pinned relatively
instead, at the same 1e-12, which the build beats by four orders of magnitude. Sine and cosine keep
the absolute tolerance because they are bounded by one. Reviewers should confirm they accept this.

Two bit-level traps had to be found and fixed before parity held, both documented at the code:

- **Floating-point contraction.** Mojo defaults to `-ffp-contract=fast` and fused
  `offset + scale * value` into an FMA. Distances were off by up to 8.9e-16 and bootstrap quantiles
  by 2.2e-16. `scripts/build_kernels.sh` now passes `--fp-mode contract=off`; that flag is a parity
  contract, not a tuning knob, and removing it silently breaks the receipt.
- **numpy's `linear` quantile is not the textbook formula.** Its virtual index is `(n - 1) * q`,
  and numpy's own source comments that it prefers this to the Hyndman and Fan form
  `n*q + (alpha + q*(1 - alpha - beta)) - 1` that its `_compute_virtual_index` spells out. Taking
  the textbook form cost 4.4e-16 on the p10 row. Its interpolation also switches association at
  `gamma >= 0.5`. Related: `0.1 * 100.0 / 100.0` is `0.10000000000000002`, so every call site
  passes `numpy.true_divide(percentages, 100.0)` rather than the tidy fraction.

## 4. Benchmark

`evidence/phase3-benchmark.md`, WSL2, 1,568 x 20 synthetic pilot, k=20, 100 queries, best of 5:

| engine | microseconds per query | versus fitted-per-query sklearn |
|---|---:|---:|
| scikit-learn brute force, fitted per query (what `find_analogs` used to do) | 653.9 | 1.00x |
| scikit-learn brute force, fitted once and batched | 9.9 | 65.92x |
| `kernels(python)` per query | 233.4 | 2.80x |
| `kernels(mojo)` per query | 70.7 | **9.25x** |

The acceptance criterion ("at least as fast as scikit-learn's brute-force `NearestNeighbors` on the
1,568-cell pilot") is met **9.25x over** against the call pattern the code actually had, and 3.3x
over the numpy reference. The batched row is reported deliberately: scikit-learn fitted once and
answering all queries in one BLAS call is 7x faster than the kernel, and the kernel has no batch
entry point. If the AnEn lane ever batches its queries, that is where the remaining headroom is,
and it is a Python-side change, not a Mojo one.

## 5. Gates, run rather than predicted

| gate | where | result |
|---|---|---|
| `ruff format --check src tests scripts` | Windows | PASS, 154 files |
| `ruff check src tests scripts` | Windows | PASS |
| `mypy src scripts` | Windows | PASS, 83 source files |
| `pytest -q` (`PLANTGEO_ML_KERNELS` unset, so python) | Windows | **683 passed, 12 skipped** -- the 12 are the Mojo parameters, skipped with a named reason |
| `pytest -q` with `PLANTGEO_ML_KERNELS=python` | WSL2 | **695 passed** |
| `pytest -q` with `PLANTGEO_ML_KERNELS=mojo` | WSL2 | **695 passed** |
| `pixi run test-kernels` | WSL2 | 12 Mojo assertions, all ok |
| `pixi run probe-kernels` | WSL2 | verdict PASS, exit 0 |
| `python scripts/check.py` (all four gates) | Windows | format PASS, lint PASS, mypy PASS, pytest PASS |

**The whole existing suite passing under `PLANTGEO_ML_KERNELS=mojo` is the strongest parity
evidence in this slice**, and it is stronger than the fixture harness: every AnEn, signal
forecaster, NDVI forecaster and fire-risk feature test that already pinned this service's numbers
passes with Mojo doing the arithmetic.

## 6. What is owed, and what a reviewer must check

1. **`QUALITY_RECEIPT.json` is stale and cannot be refreshed here.** `scripts/check.py
   --write-receipt` refuses an unstaged tree by design, and this slice is forbidden from running
   `git add`. Whoever integrates must, after staging: `uv run --no-sync python scripts/check.py
   --write-receipt`, then `git add` the receipt. Until then the image build's receipt gate fails.
2. **The Docker Mojo stage is UNPROVEN on Railway.** It is written (`Dockerfile`, stages
   `mojo-kernels-disabled` / `mojo-kernels-enabled`, selected by `--build-arg MOJO_KERNELS=`), and
   it runs exactly the commands proven in WSL2, but this box builds no container images (owner rule
   2026-09-08), so nobody has watched it run. It is **OFF by default** precisely so an unreachable
   `conda.modular.com` cannot fail a deploy of a live service. Turning it on is a build-arg change
   and reversible.
3. **Two rules were relaxed, both deliberately, both need a reviewer's eye.**
   - `tests/test_layer_import_contract.py`: `method/monte_carlo` may now import
     `method/kernels`. It previously could not, which made the dispatch unreachable from the
     estimator that needed it. `method/kernels` still cannot import either sibling, so the reverse
     edge -- the one that would break the lattice -- is untouched.
   - `scripts/quality_receipt.py`: `_native` and `__mojocache__` join `__pycache__` in the excluded
     directory names, and `.so` joins the excluded suffixes. Without this the digest would cover
     build output that is present in WSL2, present in an enabled Docker runtime and absent on
     Windows, which makes a receipt unverifiable rather than more honest. Verified: the disk walk
     now covers 177 files and no `.so` appears among them.
4. **`pyproject.toml` gained `artifacts = [".../_native/*.so"]`.** hatchling honours VCS ignore
   rules, and `_native/` is gitignored, so without this line an image that DID compile the kernels
   would install a wheel without them and `PLANTGEO_ML_KERNELS=mojo` would refuse to start. This is
   untested end to end for the same reason as point 2.
5. **`find_analogs` no longer uses scikit-learn.** It now goes through `kernels()`. The distances it
   returns are computed by a fixed-order numpy accumulation rather than by sklearn's BLAS-backed
   `ArgKmin`, so they may differ from the old values in the last bit. No test pins them exactly and
   the whole suite passes; nothing downstream consumes the distance (the caller discards it). Said
   out loud because "byte-identical to today" is true of every test, not provably true of every
   float. scikit-learn remains a dependency: `method/ml/recommendation_models.py` still uses it.
6. **`with_seasonality` moved off Polars expressions onto the kernel.** The polars chain and the
   scalar `photoperiod_seconds` helper used different association orders and could differ in the
   last bit; they cannot now. `feature_checksum` values are self-consistent and are not pinned
   anywhere, so nothing external moves.
7. **`pixi.lock` (39 KB) is new and should be committed** with `pixi.toml`; it is what makes the
   toolchain reproducible. `.pixi/` is gitignored.

## 7. Things deliberately NOT done

- No `mojo` third-party package is a dependency: no NuMojo, no Mojmelo. The kernels are hand-written
  on the stdlib, per the spec's ecosystem-lag risk.
- No RNG was ported. The bootstrap consumes the index stream numpy's seeded PCG64 emitted, which is
  why "bit-identical" is even askable of a Monte Carlo kernel.
- `PLANTGEO_ML_KERNELS=mojo` was not set on any Railway service. That waits for a parity receipt
  produced by the image itself, not by this worktree.

## p3-fix-batch

Applied 2026-09-19 in the same worktree, after the review of the section above. Seven changes; the
gates below were run, not predicted.

**M1 -- the lock is now load-bearing.** `Dockerfile` copies `pixi.toml pixi.lock` together and runs
`pixi run --locked build-kernels && pixi run --locked probe-kernels`, so a manifest edited without
re-locking fails the BUILD instead of silently re-solving to a compiler the parity receipt never
saw. `pixi.lock` is confirmed NOT gitignored (`git check-ignore` exits 1) and NOT stale:
`pixi run --locked build-kernels` succeeded against it unchanged, so no regeneration was needed.
`RUNBOOK.md` gains "`pixi.lock` pins the toolchain, and `--locked` is what makes the pin real".

**M2 -- a skip under `PLANTGEO_ML_KERNELS=mojo` is now a FAILURE.** The three-way decision lives in
`kernel_harness.native_backend_decision(environment=, failure=)`, which is pure and takes the load
failure as an argument, so both branches are testable on any box. `run` when the build loads;
`skip` with the old named reason when nobody asked for Mojo; `fail` when the operator did, because
a skip there reports green for a run in which the kernels under test never executed. Both branches
are pinned in `test_kernel_dispatch.py`, and the fail path was also demonstrated live: on Windows,
where the linux-64 `.so` files are present but unloadable, `PLANTGEO_ML_KERNELS=mojo pytest
tests/kernels` gave **16 failed, 40 passed** -- exactly the 16 parameters that skip when the
variable is unset.

**M3 -- two new cases, and the kernel needed NO change.** `kernel_cases.py` adds
`neighbor_tie_case()` (four distinct rows each repeated three times, k=5 so the cut lands inside a
block) and `neighbor_oversubscribed_case()` (k = all 40 rows, the two leakage windows leaving
exactly 5 eligible). Fixtures were regenerated from the PYTHON REFERENCE only, via the existing
`scripts/regenerate_kernel_fixtures.py`, which imports `reference` and never a backend. The Mojo
build then matched **bit-for-bit on both**, 0.0 deviation, same indices, same selected count -- so
no tie-order or truncation fix was owed. This was not luck and it is worth saying why: the
reference's `numpy.argsort(kind="stable")` and the kernel's strictly-less linear scan over
increasing rows both give the tie to the lower index, and the kernel's `Float64.MAX` sentinel is
never `< best_distance`, so the selection loop breaks at the eligible count instead of padding.
Golden ties are `[3, 4, 5, 9, 10]`; the oversubscribed case answers 5 of a requested 40.

**M4 -- non-finite arrays are refused at the bundle.** `_require_finite` now runs inside
`_require_float_vector` and `_require_float_matrix`, so EVERY float64 array crossing into a kernel
is covered in one place: `query_vector`, `candidate_matrix`, `feature_weights`,
`innovation_pools`, `path_offsets`, `innovation_scales`, `quantile_probabilities`, `year_lengths`,
`latitudes`. It raises `KernelArgumentError` built on `foundation.canonical.validate_finite`, so
the message names the offending flat index (`candidate_matrix[13] must be a finite float`), and
the check is vectorised: `numpy.isfinite(...).all()` first, `validate_finite` only on the one
element that failed. `draw_indices` is int64 and cannot be non-finite. The scalar `lower_bound`
and `upper_bound` are deliberately EXEMPT and there is a test saying so: `-inf`/`+inf` is how the
empirical-resample shape spells "do not clip". Seven new tests cover NaN and inf in both modes.

**m1 -- the tree-wide `.so` exclusion is reverted.** `scripts/quality_receipt.py` keeps only the
`_native` and `__mojocache__` DIRECTORY exclusions. A suffix rule would have let any future `.so`
anywhere under `src/`, `tests/` or `scripts/` drop out of the digest silently, which is a hole in
the gate rather than a build-artifact carve-out. Point 3 of section 6 above is superseded on this.

**m2 -- the two `HEADER_SLOTS` declarations are now asserted, not assumed.** `knn_search.mojo`
exports `describe_bundle`, which echoes the six header scalars plus the first per-candidate day
index -- read at the offset the MOJO file believes the header ends at -- and returns its own
`HEADER_SLOTS`. `native.py` gains `pack_neighbor_bundle` (extracted, so the packer is one function
the search and the probe share) and `NativeKernels.describe_neighbor_bundle`. Drift here is silent
rather than loud: the kernel would read a day index as a scalar and answer a plausible wrong
neighbour set.

**m4 -- RUNBOOK phase-3 row** now points at `evidence/phase3-benchmark.md` and carries the caveat
in the same breath: batched scikit-learn is still 7x faster than the kernel and the kernel has no
batch entry point.

### Gates, all green

| gate | where | result |
|---|---|---|
| `uv sync --locked --all-extras` | Windows | ok |
| `ruff format src tests scripts` then `--check` | Windows | PASS, 154 files |
| `ruff check src tests scripts` | Windows | PASS |
| `mypy src scripts` | Windows | PASS, 83 source files |
| `pytest -q` (python mode) | Windows | **700 passed, 16 skipped** (was 683/12) |
| `pixi run --locked build-kernels` | WSL2 | ok, lock satisfied the manifest unchanged |
| `pixi run --locked test-kernels` | WSL2 | 12 Mojo assertions, all ok |
| `pixi run --locked probe-kernels` / `parity-receipt` | WSL2 | verdict **PASS**, no failures |
| `pytest -q` with `PLANTGEO_ML_KERNELS=python` | WSL2 | **716 passed** |
| `pytest -q` with `PLANTGEO_ML_KERNELS=mojo` | WSL2 | **716 passed** |

`evidence/phase3-parity.json` is regenerated and gains `knn_search_exact_ties` and
`knn_search_oversubscribed`, both `bit_identical: true`, `indices_identical: true`,
`selected_count_identical: true`, `selected_count: 5`. `verdicts()` fails the probe on any of the
three, so the Docker gate now covers the edge cases too.

### One environment trap found the hard way

Running `uv run` from WSL2 inside this worktree **destroys the Windows `.venv`**: uv rebuilds it
for linux-64 in place, leaving a `lib64 -> lib` symlink that Windows uv then cannot remove
(`Access is denied. (os error 5)`). The WSL suite must be driven through a venv OUTSIDE the tree --
`UV_PROJECT_ENVIRONMENT=$HOME/plantgeo-ml-venv uv sync --locked --all-extras`, then
`~/plantgeo-ml-venv/bin/python -m pytest`. Recovery is `rm -rf .venv` from WSL2 (Windows cannot
delete the symlink either) followed by `uv sync` on Windows. Worth a line in the RUNBOOK if anyone
repeats the two-platform sweep.
