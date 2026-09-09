-- `drizzle/0033` pinned `SET search_path = public, pg_catalog` on five tile functions and
-- deliberately did NOT pin it on `geo.intervention_tiles`, preserving how `drizzle/0005` left that
-- one (see 0033's header and the comment above its intervention_tiles body).
--
-- `drizzle/0038` then ASSERTS that all five of sensor/fire_risk/burn_severity/evacuation_zone/
-- intervention tiles carry exactly that pin, and refuses to run otherwise. Those two facts are
-- contradictory: a database built from this tree reaches 0038 with intervention_tiles unpinned and
-- dies on
--     geo.intervention_tiles does not have the properties 0038 preserves
--
-- Production got past 0038 only because the pin was applied there BY HAND; nothing in this tree
-- reproduces it. Measured 2026-09-08 by replaying 0000..0036 onto an empty database on the
-- production server: prod's intervention_tiles carries the pin, the replayed one does not.
--
-- This migration is that hand change, recorded. It occupies the previously unused 0037 slot
-- because it must land BEFORE 0038's assertion, and it is a no-op against production, where the
-- pin is already present. ALTER FUNCTION ... SET is idempotent and rewrites no function body, so
-- it neither disturbs the 0033 body in production nor conflicts with the body 0038 installs next.
ALTER FUNCTION geo.intervention_tiles(integer, integer, integer)
  SET search_path = public, pg_catalog;
