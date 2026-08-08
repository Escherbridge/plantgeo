-- Purpose: prove the four forecast capability roles are wired exactly as this build
--          expects -- they exist, they cannot log in or inherit, nobody is a member of
--          them, and the relation / column / sequence / function privileges they hold
--          are precisely the contracted set with nothing missing and nothing extra.
--          Returns that whole judgement as a single boolean.
-- Loaded by: agri_data_service.routes.health.queries
-- Params: none. Everything this statement compares against is baked into the text at
--         import time from Python tuples (see Placeholder below); no value is bound.
--
-- Parameter and slot names appear in these comments WITHOUT a leading colon. See
-- "Header/bind-param trap" in sql/AGENTS.md: SQLAlchemy scans comments for
-- colon-prefixed words as well as real SQL, and a name written with its colon here
-- would mint a bind parameter no caller supplies.
--
-- Placeholder: this file is .format()ted once, at module import time, before it is
--         handed to text(). Its named slots -- written in curly braces in the VALUES
--         lists and the role count -- are filled from Python constants in
--         agri_data_service.routes.health.contracts, rendered into SQL row-literals by
--         _sql_values in agri_data_service.routes.health.queries. None is ever request
--         input; they are this build's hard-coded role contract, which is what makes
--         baking them into the statement text legal rather than a SQL-injection
--         surface. The slots are:
--           forecast_role_values                FORECAST_ROLES
--           forecast_relation_privilege_values  FORECAST_ROLE_RELATION_PRIVILEGES
--           forecast_column_privilege_values    FORECAST_ROLE_COLUMN_PRIVILEGES
--           forecast_sequence_privilege_values  FORECAST_ROLE_SEQUENCE_PRIVILEGES
--           forecast_function_privilege_values  FORECAST_ROLE_FUNCTION_PRIVILEGES
--           table_privilege_values              TABLE_PRIVILEGE_UNIVERSE
--           column_privilege_values             COLUMN_PRIVILEGE_UNIVERSE
--           sequence_privilege_values           SEQUENCE_PRIVILEGE_UNIVERSE
--           forecast_role_count                 len(FORECAST_ROLES)
--         Because .format() consumes curly braces, any literal brace added to this file
--         later must be DOUBLED or the format call raises KeyError and the whole
--         service fails to import. There are none today.
--
-- Why this exists separately from health_readiness.sql, which asks the same questions:
-- the readiness statement folds this judgement into its forecast_roles_ready column
-- alongside assertions about the *calling* login, and the published_reader profile can
-- read that column directly. The receiver_writer login is not a member of the forecast
-- roles at all, so the route runs this standalone version for it and reports the answer
-- as its own check. Keep the two in step: a change to the role contract belongs in both.
--
-- What this returns: exactly one row, one boolean column
-- forecast_role_contracts_ready. False is a refusal to serve, not an error.
--
-- Vocabulary, explained once, because every section below reuses it. The fuller
-- treatment of each construct is in health_readiness.sql:
--
--   WITH name(column) AS (VALUES ('a'), ('b'))
--     A CTE ("common table expression") is a named subquery defined up front and used
--     below like a table; VALUES writes a literal table straight into the query text.
--     Every expectation in this file arrives this way, rendered from Python tuples at
--     import time, so the query can compare the live database against the contract.
--
--   has_table_privilege(who, what, 'SELECT') and its siblings
--     Ask PostgreSQL "may this role do this to this object?" and get true/false. One
--     exists per object class: has_database_privilege, has_schema_privilege,
--     has_table_privilege, has_column_privilege, has_sequence_privilege,
--     has_function_privilege. They answer the effective question, counting privileges
--     reached through role membership.
--
--   ... 'EXECUTE WITH GRANT OPTION'
--     Appending WITH GRANT OPTION asks whether the role may hand that privilege on to
--     someone else. A capability role must hold its privileges and be unable to give
--     them away, so every assertion here is doubled: hold it, and not be able to grant
--     it.
--
--   coalesce(has_table_privilege(...), false)
--     coalesce returns its first non-NULL argument. These functions return NULL when
--     handed a NULL object identifier, and SQL's three-valued logic would let that NULL
--     propagate through the surrounding AND-chain and make the whole answer NULL rather
--     than false. coalesce makes a missing object read as "no privilege".
--
--   NOT EXISTS (SELECT 1 FROM ... WHERE ...)
--     "No row matches this" -- each one is a prohibition naming a condition that must
--     never be found. SELECT 1 is idiomatic; EXISTS only cares that a row came back.
--
--   CROSS JOIN
--     Every combination of left rows with right rows, no join condition. Pairing the
--     objects that exist against the universe of privileges that could be granted is
--     how this file asks the "and nothing more" half of the question -- it enumerates
--     every possible grant and requires each to match the contract.
--
--   IS DISTINCT FROM
--     Inequality that treats NULL as an ordinary value, so a NULL on either side is
--     still reported as a difference instead of vanishing into NULL as plain <> would.
--
--   aclexplode(acl), grantee = 0, acldefault('f', owner)
--     An access control list is one opaque array per object; aclexplode expands it into
--     one row per grant. Grantee 0 is PUBLIC, meaning everybody -- a grant to PUBLIC is
--     invisible to checks aimed at named roles. acldefault rebuilds the built-in
--     default ACL that PostgreSQL stores as NULL when nobody has modified it; for
--     functions that default includes EXECUTE to PUBLIC, so without it every untouched
--     function would escape this probe.
--
--   to_regclass(...) / to_regprocedure(...)
--     Resolve an object or function signature by name to its internal identifier, or
--     NULL when it does not exist -- turning "missing" from a parse error into a
--     testable value.
--
-- How this query works, section by section:
--
--   forecast_role_names / forecast_roles
--     The four expected role names, LEFT JOINed to pg_roles. LEFT JOIN keeps the
--     expected name even when the role is absent, leaving role_oid NULL; counting
--     non-NULL oids against forecast_role_count is how a missing role is caught.
--
--   forecast_relation_privileges / forecast_column_privileges /
--   forecast_sequence_privileges / forecast_function_privileges
--     The contracted matrix as literal tables: which role may do what to which
--     relation, column, sequence and function.
--
--   table_privilege_universe / column_privilege_universe / sequence_privilege_universe
--     Every privilege each object class can carry, present only to be CROSS JOINed
--     against real objects so unexpected grants can be detected.
--
--   agri_relations / agri_relation_columns / forecast_sequences / forecast_functions
--     What schema agri actually contains, from the system catalogs. relkind filters
--     pg_class by kind -- 'r' table, 'p' partitioned table, 'v' view, 'm' materialized
--     view, 'f' foreign table, 'S' sequence. attnum > 0 skips hidden system columns and
--     NOT attisdropped skips dropped-but-still-present column slots.
--
--   The single SELECT
--     One AND-chain, each link a separate requirement. All four roles exist. None can
--     log in, inherit, or hold any admin attribute (superuser, createdb, createrole,
--     replication, bypassrls) -- a capability role is a privilege bundle, never an
--     identity. Nobody is a member of any of them here, and no membership anywhere
--     carries admin_option. Each role holds CONNECT and schema USAGE, and neither
--     CREATE nor a grant option. Then four exact-match blocks, one per object class:
--     the privileges actually held are IS DISTINCT FROM nothing in the contract, so
--     neither a missing grant nor an extra one passes, and no grant option exists
--     anywhere. Then the PUBLIC probe: no agri function may be executable by everybody.
--     Finally three existence checks confirm the contract still names real objects -- a
--     relation, sequence or function signature that no longer resolves means the
--     contract has drifted from the schema, and the matching block above it would
--     otherwise be silently vacuous.
WITH forecast_role_names(role_name) AS (
    VALUES {forecast_role_values}
),
forecast_roles AS (
    SELECT expected.role_name, role.oid AS role_oid
    FROM forecast_role_names AS expected
    LEFT JOIN pg_roles AS role ON role.rolname = expected.role_name
),
forecast_relation_privileges(role_name, schema_name, relation_name, privilege_name) AS (
    VALUES {forecast_relation_privilege_values}
),
forecast_column_privileges(role_name, schema_name, relation_name, column_name, privilege_name) AS (
    VALUES {forecast_column_privilege_values}
),
forecast_sequence_privileges(role_name, schema_name, sequence_name, privilege_name) AS (
    VALUES {forecast_sequence_privilege_values}
),
forecast_function_privileges(role_name, function_signature, privilege_name) AS (
    VALUES {forecast_function_privilege_values}
),
table_privilege_universe(privilege_name) AS (
    VALUES {table_privilege_values}
),
column_privilege_universe(privilege_name) AS (
    VALUES {column_privilege_values}
),
sequence_privilege_universe(privilege_name) AS (
    VALUES {sequence_privilege_values}
),
agri_relations AS (
    SELECT class.oid, class.relname AS relation_name
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri'
      AND class.relkind IN ('r', 'p', 'v', 'm', 'f')
),
agri_relation_columns AS (
    SELECT relation.oid, relation.relation_name, attribute.attname AS column_name
    FROM agri_relations AS relation
    INNER JOIN pg_attribute AS attribute ON attribute.attrelid = relation.oid
    WHERE attribute.attnum > 0 AND NOT attribute.attisdropped
),
forecast_sequences AS (
    SELECT class.oid, class.relname AS sequence_name
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri'
      AND class.relkind = 'S'
),
forecast_functions AS (
    SELECT procedure.oid, procedure.proacl, procedure.proowner
    FROM pg_proc AS procedure
    INNER JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'agri'
)
SELECT
    (SELECT count(role_oid) = {forecast_role_count} FROM forecast_roles)
    AND NOT EXISTS (
        SELECT 1
        FROM pg_roles AS role
        INNER JOIN forecast_roles AS expected ON expected.role_oid = role.oid
        WHERE role.rolcanlogin OR role.rolinherit OR role.rolsuper OR role.rolcreatedb
           OR role.rolcreaterole OR role.rolreplication OR role.rolbypassrls
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        INNER JOIN pg_roles AS member_role ON member_role.oid = membership.member
        INNER JOIN forecast_roles AS expected ON expected.role_oid = member_role.oid
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        INNER JOIN forecast_roles AS capability ON capability.role_oid = membership.roleid
        WHERE membership.admin_option
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_roles AS role
        WHERE role.role_oid IS NOT NULL
          AND (
              NOT has_database_privilege(role.role_oid, current_database(), 'CONNECT')
              OR has_database_privilege(role.role_oid, current_database(), 'CREATE')
              OR NOT has_schema_privilege(role.role_oid, 'agri', 'USAGE')
              OR has_schema_privilege(role.role_oid, 'agri', 'CREATE')
              OR has_schema_privilege(role.role_oid, 'agri', 'USAGE WITH GRANT OPTION')
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_roles AS role
        CROSS JOIN agri_relations AS relation
        CROSS JOIN table_privilege_universe AS candidate
        WHERE role.role_oid IS NOT NULL
          AND (
              coalesce(
                  has_table_privilege(role.role_oid, relation.oid, candidate.privilege_name),
                  false
              ) IS DISTINCT FROM EXISTS (
                  SELECT 1
                  FROM forecast_relation_privileges AS expected
                  WHERE expected.role_name = role.role_name
                    AND expected.schema_name = 'agri'
                    AND expected.relation_name = relation.relation_name
                    AND expected.privilege_name = candidate.privilege_name
              )
              OR coalesce(
                  has_table_privilege(
                      role.role_oid,
                      relation.oid,
                      candidate.privilege_name || ' WITH GRANT OPTION'
                  ),
                  false
              )
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_roles AS role
        CROSS JOIN agri_relation_columns AS column_object
        CROSS JOIN column_privilege_universe AS candidate
        WHERE role.role_oid IS NOT NULL
          AND (
              coalesce(
                  has_column_privilege(
                      role.role_oid,
                      column_object.oid,
                      column_object.column_name,
                      candidate.privilege_name
                  ),
                  false
              ) IS DISTINCT FROM (
                  EXISTS (
                      SELECT 1
                      FROM forecast_relation_privileges AS expected
                      WHERE expected.role_name = role.role_name
                        AND expected.schema_name = 'agri'
                        AND expected.relation_name = column_object.relation_name
                        AND expected.privilege_name = candidate.privilege_name
                  )
                  OR EXISTS (
                      SELECT 1
                      FROM forecast_column_privileges AS expected
                      WHERE expected.role_name = role.role_name
                        AND expected.schema_name = 'agri'
                        AND expected.relation_name = column_object.relation_name
                        AND expected.column_name = column_object.column_name
                        AND expected.privilege_name = candidate.privilege_name
                  )
              )
              OR coalesce(
                  has_column_privilege(
                      role.role_oid,
                      column_object.oid,
                      column_object.column_name,
                      candidate.privilege_name || ' WITH GRANT OPTION'
                  ),
                  false
              )
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_roles AS role
        CROSS JOIN forecast_sequences AS sequence_object
        CROSS JOIN sequence_privilege_universe AS candidate
        WHERE role.role_oid IS NOT NULL
          AND (
              coalesce(
                  has_sequence_privilege(role.role_oid, sequence_object.oid, candidate.privilege_name),
                  false
              ) IS DISTINCT FROM EXISTS (
                  SELECT 1
                  FROM forecast_sequence_privileges AS expected
                  WHERE expected.role_name = role.role_name
                    AND expected.schema_name = 'agri'
                    AND expected.sequence_name = sequence_object.sequence_name
                    AND expected.privilege_name = candidate.privilege_name
              )
              OR coalesce(
                  has_sequence_privilege(
                      role.role_oid,
                      sequence_object.oid,
                      candidate.privilege_name || ' WITH GRANT OPTION'
                  ),
                  false
              )
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_roles AS role
        CROSS JOIN forecast_functions AS function_object
        WHERE role.role_oid IS NOT NULL
          AND (
              coalesce(
                  has_function_privilege(role.role_oid, function_object.oid, 'EXECUTE'),
                  false
              ) IS DISTINCT FROM EXISTS (
                  SELECT 1
                  FROM forecast_function_privileges AS expected
                  WHERE expected.role_name = role.role_name
                    AND to_regprocedure(expected.function_signature) = function_object.oid
                    AND expected.privilege_name = 'EXECUTE'
              )
              OR coalesce(
                  has_function_privilege(
                      role.role_oid,
                      function_object.oid,
                      'EXECUTE WITH GRANT OPTION'
                  ),
                  false
              )
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM forecast_functions AS function_object
        WHERE EXISTS (
            SELECT 1
            FROM aclexplode(coalesce(function_object.proacl, acldefault('f', function_object.proowner))) AS access
            WHERE access.grantee = 0 AND access.privilege_type = 'EXECUTE'
        )
    )
    AND NOT EXISTS (
        SELECT 1 FROM forecast_relation_privileges
        WHERE to_regclass(format('%I.%I', schema_name, relation_name)) IS NULL
    )
    AND NOT EXISTS (
        SELECT 1 FROM forecast_sequence_privileges
        WHERE to_regclass(format('%I.%I', schema_name, sequence_name)) IS NULL
    )
    AND NOT EXISTS (
        SELECT 1 FROM forecast_function_privileges
        WHERE to_regprocedure(function_signature) IS NULL
    ) AS forecast_role_contracts_ready
