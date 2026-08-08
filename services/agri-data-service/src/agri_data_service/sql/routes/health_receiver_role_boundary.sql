-- Purpose: prove the receiver_writer login is boxed in. It must belong to no role, own
--          nothing, hold exactly the table and sequence privileges the publication
--          contract lists and no others, and be able to EXECUTE no function in schema
--          agri at all. Returns that whole judgement as a single boolean.
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
--         lists -- are filled from Python constants in
--         agri_data_service.routes.health.contracts, rendered into SQL row-literals by
--         _sql_values in agri_data_service.routes.health.queries. None is ever request
--         input; they are this build's hard-coded privilege contract, which is what
--         makes baking them into the statement text legal rather than a SQL-injection
--         surface. The slots are:
--           receiver_relation_privilege_values  PUBLICATION_TABLE_PRIVILEGES
--           receiver_sequence_privilege_values  RECEIVER_SEQUENCE_PRIVILEGES
--           table_privilege_values              TABLE_PRIVILEGE_UNIVERSE
--           column_privilege_values             COLUMN_PRIVILEGE_UNIVERSE
--           sequence_privilege_values           SEQUENCE_PRIVILEGE_UNIVERSE
--         Because .format() consumes curly braces, any literal brace added to this file
--         later must be DOUBLED or the format call raises KeyError and the whole
--         service fails to import. There are none today.
--
-- Why it exists: the receiver is the only authenticated write path into the operational
-- database, so its blast radius is its privilege set. Confirming the grants it needs
-- are present is the easy half; this statement's real job is the other half -- proving
-- nothing else was ever granted, inherited, or acquired through ownership. The route
-- ANDs this answer with the readiness statement's privileges_ready column, so both must
-- hold before the receiver profile reports ready.
--
-- What this returns: exactly one row, one boolean column
-- receiver_role_boundary_ready. False is a refusal to serve, not an error.
--
-- Vocabulary, explained once, because every section below reuses it. The fuller
-- treatment of each construct is in health_readiness.sql:
--
--   WITH name(column) AS (VALUES ('a'), ('b'))
--     A CTE ("common table expression") is a named subquery defined up front and used
--     below like a table; VALUES writes a literal table straight into the query text.
--     The expected privilege sets arrive this way, rendered from Python tuples at
--     import time, so the query can compare the live database against the contract.
--
--   has_table_privilege(who, what, 'SELECT') and its siblings
--     Ask PostgreSQL "may this role do this to this object?" and get true/false. One
--     exists per object class: has_table_privilege, has_any_column_privilege,
--     has_sequence_privilege, has_function_privilege. They answer the effective
--     question, counting privileges reached through role membership -- which is what
--     the live connection can really do.
--
--   has_any_column_privilege(who, relation, 'SELECT')
--     A column-level grant does not show up in has_table_privilege: granting SELECT on
--     one column of a table leaves the table-level answer false. This variant asks
--     whether the role holds the privilege on *any* column, and it is what closes that
--     gap -- without it a per-column grant would be an invisible hole in the boundary.
--
--   ... 'SELECT WITH GRANT OPTION'
--     Appending WITH GRANT OPTION asks whether the role may hand the privilege on to
--     somebody else. The receiver must never be able to widen anyone's access, so every
--     assertion here is doubled: hold exactly the contracted privilege, and hold the
--     grant option on nothing.
--
--   coalesce(has_table_privilege(...), false)
--     coalesce returns its first non-NULL argument. These functions return NULL when
--     handed a NULL object identifier, and SQL's three-valued logic would let that NULL
--     propagate through the surrounding AND-chain and make the whole answer NULL rather
--     than false. coalesce makes a missing object read as "no privilege".
--
--   NOT EXISTS (SELECT 1 FROM ... WHERE ...)
--     "No row matches this" -- the entire statement is built from these, because a
--     boundary is a list of things that must not be true. SELECT 1 is idiomatic; EXISTS
--     only cares that a row came back.
--
--   CROSS JOIN
--     Every combination of left rows with right rows, no join condition. Pairing the
--     objects that exist against the universe of privileges that could be granted is
--     how the "and nothing more" half of the question gets asked -- it enumerates every
--     possible grant on every object and requires each to match the contract.
--
--   IS DISTINCT FROM
--     Inequality that treats NULL as an ordinary value, so a NULL on either side is
--     still reported as a difference instead of vanishing into NULL as plain <> would.
--
--   pg_has_role(who, owner_oid, 'MEMBER')
--     Effective membership, following chains of nested roles. Used only negatively:
--     ownership carries implicit rights that no REVOKE can remove -- an owner can
--     always re-grant itself anything, and can DROP the object outright -- so reaching
--     an owner would make every privilege assertion above it meaningless.
--
--   to_regclass('schema.name')
--     Resolve an object by name to its internal identifier, or NULL when it does not
--     exist -- turning "missing" from a parse error into a testable value.
--
-- How this query works, section by section:
--
--   expected_relation_privileges / expected_sequence_privileges
--     The contract as literal tables: the (schema, relation, privilege) rows the
--     receiver must hold, and the (schema, sequence, privilege) rows it needs to claim
--     surrogate keys for observations and drought snapshots.
--
--   table_privilege_universe / column_privilege_universe / sequence_privilege_universe
--     Every privilege each object class can carry, present only to be CROSS JOINed
--     against real objects so unexpected grants can be detected.
--
--   agri_relations / agri_sequences / agri_functions
--     What schema agri actually contains, from the system catalogs, each carrying its
--     owner so the ownership probes below can use it. relkind filters pg_class by kind
--     -- 'r' table, 'p' partitioned table, 'v' view, 'm' materialized view, 'f' foreign
--     table, 'S' sequence.
--
--   The single SELECT
--     One AND-chain of prohibitions. The login is a member of no role whatsoever, so it
--     can inherit nothing. It does not reach the database owner (pg_database.datdba),
--     the agri schema owner (pg_namespace.nspowner), or the owner of any relation,
--     sequence or function in agri. Its table privileges match expected_relation_
--     privileges exactly -- IS DISTINCT FROM catches both a missing grant and an extra
--     one -- with no grant option anywhere. Its column-level privileges are held only
--     where the table-level contract already allows that privilege, which is what stops
--     a narrow per-column grant from slipping past the table-level comparison. Its
--     sequence privileges match expected_sequence_privileges exactly, again with no
--     grant option. It can EXECUTE no agri function at all. Finally two existence
--     checks confirm the contract still names real objects: a relation or sequence that
--     no longer resolves means the contract has drifted from the schema, and the
--     comparisons above it would otherwise be silently vacuous.
WITH expected_relation_privileges(schema_name, relation_name, privilege_name) AS (
    VALUES {receiver_relation_privilege_values}
),
expected_sequence_privileges(schema_name, sequence_name, privilege_name) AS (
    VALUES {receiver_sequence_privilege_values}
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
    SELECT class.oid, class.relname AS relation_name, class.relowner
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri'
      AND class.relkind IN ('r', 'p', 'v', 'm', 'f')
),
agri_sequences AS (
    SELECT class.oid, class.relname AS sequence_name, class.relowner
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri' AND class.relkind = 'S'
),
agri_functions AS (
    SELECT procedure.oid, procedure.proowner
    FROM pg_proc AS procedure
    INNER JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'agri'
)
SELECT
    NOT EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        INNER JOIN pg_roles AS member_role ON member_role.oid = membership.member
        WHERE member_role.rolname = current_user
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
        SELECT 1 FROM agri_relations AS relation
        WHERE pg_has_role(current_user, relation.relowner, 'MEMBER')
    )
    AND NOT EXISTS (
        SELECT 1 FROM agri_sequences AS sequence_object
        WHERE pg_has_role(current_user, sequence_object.relowner, 'MEMBER')
    )
    AND NOT EXISTS (
        SELECT 1 FROM agri_functions AS function_object
        WHERE pg_has_role(current_user, function_object.proowner, 'MEMBER')
    )
    AND NOT EXISTS (
        SELECT 1
        FROM agri_relations AS relation
        CROSS JOIN table_privilege_universe AS candidate
        WHERE (
            coalesce(
                has_table_privilege(current_user, relation.oid, candidate.privilege_name),
                false
            ) IS DISTINCT FROM EXISTS (
                SELECT 1 FROM expected_relation_privileges AS expected
                WHERE expected.schema_name = 'agri'
                  AND expected.relation_name = relation.relation_name
                  AND expected.privilege_name = candidate.privilege_name
            )
            OR coalesce(
                has_table_privilege(
                    current_user,
                    relation.oid,
                    candidate.privilege_name || ' WITH GRANT OPTION'
                ),
                false
            )
        )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM agri_relations AS relation
        CROSS JOIN column_privilege_universe AS candidate
        WHERE (
            has_any_column_privilege(current_user, relation.oid, candidate.privilege_name)
            AND NOT EXISTS (
                SELECT 1 FROM expected_relation_privileges AS expected
                WHERE expected.schema_name = 'agri'
                  AND expected.relation_name = relation.relation_name
                  AND expected.privilege_name = candidate.privilege_name
            )
        )
        OR has_any_column_privilege(
            current_user,
            relation.oid,
            candidate.privilege_name || ' WITH GRANT OPTION'
        )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM agri_sequences AS sequence_object
        CROSS JOIN sequence_privilege_universe AS candidate
        WHERE (
            coalesce(
                has_sequence_privilege(current_user, sequence_object.oid, candidate.privilege_name),
                false
            ) IS DISTINCT FROM EXISTS (
                SELECT 1 FROM expected_sequence_privileges AS expected
                WHERE expected.schema_name = 'agri'
                  AND expected.sequence_name = sequence_object.sequence_name
                  AND expected.privilege_name = candidate.privilege_name
            )
            OR coalesce(
                has_sequence_privilege(
                    current_user,
                    sequence_object.oid,
                    candidate.privilege_name || ' WITH GRANT OPTION'
                ),
                false
            )
        )
    )
    AND NOT EXISTS (
        SELECT 1 FROM agri_functions AS function_object
        WHERE has_function_privilege(current_user, function_object.oid, 'EXECUTE')
    )
    AND NOT EXISTS (
        SELECT 1 FROM expected_relation_privileges AS expected
        WHERE to_regclass(format('%I.%I', expected.schema_name, expected.relation_name)) IS NULL
    )
    AND NOT EXISTS (
        SELECT 1 FROM expected_sequence_privileges AS expected
        WHERE to_regclass(format('%I.%I', expected.schema_name, expected.sequence_name)) IS NULL
    ) AS receiver_role_boundary_ready
