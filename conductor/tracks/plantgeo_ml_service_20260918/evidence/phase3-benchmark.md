# Phase 3 benchmark: Mojo neighbour search against scikit-learn brute force

Measured 2026-09-19 on Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.39, 8 logical processors, CPython 3.12.14, numpy 2.5.3.

Synthetic pilot: 1568 candidate rows x 20 features, k=20, a 30-day exclusion window and a 30-day horizon guard, 100 queries per repetition, best of 5 repetitions.

| engine | seconds per repetition | microseconds per query | versus fitted-per-query sklearn |
|---|---:|---:|---:|
| scikit-learn brute force, fitted per query | 0.0654 | 653.9 | 1.00x |
| scikit-learn brute force, fitted once and batched | 0.0010 | 9.9 | 65.92x |
| kernels(python), per query | 0.0233 | 233.4 | 2.80x |
| kernels(mojo), per query | 0.0071 | 70.7 | 9.25x |

`kernels(mojo)` and `kernels(python)` return bit-identical distances and indices; the
parity receipt is `phase3-parity.json`. The batched scikit-learn row is that library's
best case and has no kernel equivalent, because `find_analogs` is called one query at a
time; it is here so the comparison cannot be accused of choosing a weak baseline.

Regenerate with `pixi run bench-kernels` from `services/plantgeo-ml-service` in WSL2.
