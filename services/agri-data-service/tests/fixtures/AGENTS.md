# Frozen historical fixtures

`historical_plans/` contains only immutable regression inputs: the real POWER and ERA5-Land support
lattices the climate and soil writer tests read (`tests/direct/{climate,soil}/conftest.py`). The
plan-continuation radiation plan left with `execution/plan_continuation.py` (purged 2026-10-26). These files
must never be treated as runnable backfill plans or copied into a deployed image.
