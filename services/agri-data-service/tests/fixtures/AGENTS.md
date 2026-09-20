# Frozen historical fixtures

`historical_plans/` contains only immutable regression inputs needed to test support geometry and
plan-continuation behavior after the service-local operational plan tree was retired. These files
must never be treated as runnable backfill plans or copied into a deployed image.
