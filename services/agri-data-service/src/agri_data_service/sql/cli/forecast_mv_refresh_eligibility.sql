-- Purpose: prove the login this command connected as is the dedicated, deliberately powerless
--          forecast materialized-view refresher, and nothing more, before it is allowed to assume
--          the refresh capability role.
-- Loaded by: agri_data_service.cli
-- Params: (none). The statement reads current_user and current_database() from the session itself,
--         so there is nothing for a caller to pass -- and nothing a caller could pass wrongly.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: exactly one row holding one boolean. TRUE means every condition below held and
-- the refresh may proceed. FALSE means at least one failed. NO ROWS means the connected role does
-- not exist in pg_roles at all. The caller treats anything that is not literally TRUE as a refusal,
-- so the no-rows case fails closed rather than being read as "fine".
--
-- Why the query is one giant AND: refreshing a materialized view is the only thing this operator is
-- meant to be able to do. Everything else it could possibly be able to do is a hole. Rather than
-- trusting that the role was created correctly once, this asks the live catalog, at connect time,
-- whether the role is still shaped the way the review said it must be. Each AND below closes one
-- specific hole; read them as a checklist, not as one condition.
--
-- How this query works, clause by clause:
--
--   FROM pg_roles AS role WHERE role.rolname = current_user
--     pg_roles is PostgreSQL's catalog of login and group roles -- the server's own table of who
--     exists. current_user is the role this session authenticated as. So the whole statement looks
--     at exactly one row: the connected role's own catalog entry.
--
--   role.rolcanlogin AND NOT role.rolinherit AND NOT role.rolsuper AND NOT role.rolcreatedb
--   AND NOT role.rolcreaterole AND NOT role.rolreplication AND NOT role.rolbypassrls
--     The role's own attribute flags. It must be able to log in, and must NOT be a superuser, must
--     not create databases or roles, must not replicate, and must not bypass row-level security.
--     rolinherit is the subtle one: a role with inherit ON silently holds the privileges of every
--     group it belongs to, all the time. NOINHERIT means it holds none of them until it explicitly
--     runs SET ROLE -- which is what makes the refresh capability a deliberate, auditable act rather
--     than an ambient one.
--
--   AND EXISTS (SELECT 1 FROM pg_auth_members AS membership ... capability.rolname =
--               'plantgeo_forecast_mv_refresher' AND NOT membership.admin_option
--               AND NOT membership.inherit_option AND membership.set_option)
--     EXISTS is a test, not a fetch: it asks "does at least one row match?" and stops at the first
--     one, so the SELECT 1 inside is a placeholder -- no column value is ever used. pg_auth_members
--     is the catalog of role-in-role memberships, joined to pg_roles to turn the member's numeric
--     role id into a readable name. This requires the connected role to be a member of the refresher
--     capability role, and requires that membership to be the weakest kind that still works:
--     set_option (it may SET ROLE to it) but NOT admin_option (it may not grant that role onward to
--     anyone else) and NOT inherit_option (it does not passively hold the role's rights).
--
--   AND NOT EXISTS (SELECT 1 FROM pg_auth_members ... capability.rolname <>
--                   'plantgeo_forecast_mv_refresher')
--     The mirror of the clause above, and the reason both are needed. The first says "it has this
--     membership"; this one says "and no others". Without it, the operator could be granted a second
--     role tomorrow and this check would still pass.
--
--   AND NOT has_database_privilege(current_user, current_database(), 'CREATE')
--     A built-in function that answers a privilege question directly. The role must not be able to
--     create new schemas in this database.
--
--   AND NOT pg_has_role(current_user, (SELECT database.datdba FROM pg_database ...), 'MEMBER')
--   AND NOT pg_has_role(current_user, (SELECT namespace.nspowner FROM pg_namespace ...), 'MEMBER')
--     pg_has_role asks whether one role is a member of another. datdba is the database's owning
--     role and nspowner is the agri schema's owning role. An owner can do anything to what it owns
--     regardless of granted privileges, so being (even indirectly) the owner would make every other
--     check here decorative. The scalar subqueries look those two owners up by name.
--
--   AND NOT EXISTS (... pg_class ... pg_has_role(current_user, class.relowner, 'MEMBER'))
--   AND NOT EXISTS (... pg_proc ... pg_has_role(current_user, procedure.proowner, 'MEMBER'))
--     The same ownership argument one level down: pg_class is the catalog of tables, views,
--     materialized views and sequences; pg_proc is the catalog of functions and procedures. Both are
--     joined to pg_namespace so the search can be restricted to the agri schema. The role must not
--     own -- and must not be a member of the owner of -- any object or routine in it.
--
--   AND NOT EXISTS (... CROSS JOIN LATERAL aclexplode(coalesce(class.relacl, acldefault(...)))
--                   AS access WHERE ... access.grantee = role.oid)
--     Ownership is not the only way to hold power over an object; an explicit GRANT is the other.
--     An object's grants live in a single packed "access control list" column (relacl for tables,
--     proacl for routines, nspacl for schemas, attacl for individual columns), which cannot be
--     queried with a plain WHERE. aclexplode is a set-returning function that unpacks that one value
--     into one row per grant, with a grantee column naming who received it. LATERAL is what lets it
--     see the row it is being joined to -- an ordinary subquery cannot reference the current row --
--     and CROSS JOIN LATERAL means "for each object row, expand its ACL and consider those rows".
--     coalesce(..., acldefault(...)) covers the case where the column is NULL, which does not mean
--     "no grants": it means "the built-in defaults apply", and acldefault materializes what those
--     defaults are so they are checked rather than skipped. The relkind CASE picks the sequence
--     defaults ('S') for sequences and the ordinary relation defaults ('r') for everything else,
--     because the two have different built-in grants.
--
--   AND NOT EXISTS (... pg_attribute AS attribute ... aclexplode(attribute.attacl) ...)
--     The same ACL check at column granularity, since a grant can name a single column rather than a
--     whole table. attnum > 0 skips PostgreSQL's internal system columns, which have negative
--     numbers, and NOT attisdropped skips columns that were dropped -- their catalog rows survive as
--     tombstones and would otherwise be inspected as if they were real.
--
--   AND NOT EXISTS (... pg_namespace ... aclexplode(coalesce(namespace.nspacl,
--                   acldefault('n', namespace.nspowner))) ...)
--     And once more for the agri schema itself, so a schema-level grant cannot slip past the
--     object-level and column-level checks above.
SELECT
    role.rolcanlogin
    AND NOT role.rolinherit
    AND NOT role.rolsuper
    AND NOT role.rolcreatedb
    AND NOT role.rolcreaterole
    AND NOT role.rolreplication
    AND NOT role.rolbypassrls
    AND EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        INNER JOIN pg_roles AS capability
            ON capability.oid = membership.roleid
        WHERE membership.member = role.oid
          AND capability.rolname = 'plantgeo_forecast_mv_refresher'
          AND NOT membership.admin_option
          AND NOT membership.inherit_option
          AND membership.set_option
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        INNER JOIN pg_roles AS capability
            ON capability.oid = membership.roleid
        WHERE membership.member = role.oid
          AND capability.rolname <> 'plantgeo_forecast_mv_refresher'
    )
    AND NOT has_database_privilege(
        current_user,
        current_database(),
        'CREATE'
    )
    AND NOT pg_has_role(
        current_user,
        (SELECT database.datdba FROM pg_database AS database
         WHERE database.datname = current_database()),
        'MEMBER'
    )
    AND NOT pg_has_role(
        current_user,
        (SELECT namespace.nspowner FROM pg_namespace AS namespace
         WHERE namespace.nspname = 'agri'),
        'MEMBER'
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_class AS class
        INNER JOIN pg_namespace AS namespace
            ON namespace.oid = class.relnamespace
        WHERE namespace.nspname = 'agri'
          AND pg_has_role(current_user, class.relowner, 'MEMBER')
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_proc AS procedure
        INNER JOIN pg_namespace AS namespace
            ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname = 'agri'
          AND pg_has_role(current_user, procedure.proowner, 'MEMBER')
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_class AS class
        INNER JOIN pg_namespace AS namespace
            ON namespace.oid = class.relnamespace
        CROSS JOIN LATERAL aclexplode(
            coalesce(
                class.relacl,
                acldefault(
                    CASE WHEN class.relkind = 'S' THEN 'S'::"char" ELSE 'r'::"char" END,
                    class.relowner
                )
            )
        ) AS access
        WHERE namespace.nspname = 'agri'
          AND access.grantee = role.oid
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_proc AS procedure
        INNER JOIN pg_namespace AS namespace
            ON namespace.oid = procedure.pronamespace
        CROSS JOIN LATERAL aclexplode(
            coalesce(
                procedure.proacl,
                acldefault('f', procedure.proowner)
            )
        ) AS access
        WHERE namespace.nspname = 'agri'
          AND access.grantee = role.oid
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_attribute AS attribute
        INNER JOIN pg_class AS class
            ON class.oid = attribute.attrelid
        INNER JOIN pg_namespace AS namespace
            ON namespace.oid = class.relnamespace
        CROSS JOIN LATERAL aclexplode(attribute.attacl) AS access
        WHERE namespace.nspname = 'agri'
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND access.grantee = role.oid
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_namespace AS namespace
        CROSS JOIN LATERAL aclexplode(
            coalesce(
                namespace.nspacl,
                acldefault('n', namespace.nspowner)
            )
        ) AS access
        WHERE namespace.nspname = 'agri'
          AND access.grantee = role.oid
    )
FROM pg_roles AS role
WHERE role.rolname = current_user
