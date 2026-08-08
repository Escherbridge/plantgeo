-- Purpose: the single probe /ready runs against whichever production database profile
--          this process was started with. It answers five independent yes/no questions
--          about the database it just connected to -- are the extensions installed, is
--          the migration catalog reachable, do the tables the routes touch exist, does
--          this login hold exactly the privileges it should and nothing more, and are
--          the four forecast capability roles wired exactly as this build expects --
--          and returns them as five boolean columns of one row.
-- Loaded by: agri_data_service.routes.health.queries
-- Params: none. This statement binds no parameters at all; every expectation it
--         compares the live database against is baked into the text at import time
--         from Python tuples (see Placeholder below).
--
-- Parameter and slot names appear in these comments WITHOUT a leading colon. See
-- "Header/bind-param trap" in sql/AGENTS.md: SQLAlchemy scans comments for
-- colon-prefixed words as well as real SQL, and a name written with its colon here
-- would mint a bind parameter no caller supplies, failing every execution.
--
-- Placeholder: this file is .format()ted once, at module import time, before it is
--         handed to text(). The named slots below -- written in curly braces in the
--         VALUES lists and in the two count comparisons -- are filled from Python
--         constants in agri_data_service.routes.health.contracts, rendered into SQL
--         row-literals by _sql_values in agri_data_service.routes.health.queries. None
--         of them is ever request input; they are this build's hard-coded readiness
--         contract, which is what makes baking them into the statement text legal
--         rather than a SQL-injection surface. The slots are:
--           extension_values                    REQUIRED_EXTENSIONS
--           privilege_values                    PUBLICATION_TABLE_PRIVILEGES
--                                               + MIGRATION_TABLE_PRIVILEGES
--           table_privilege_values              TABLE_PRIVILEGE_UNIVERSE
--           column_privilege_values             COLUMN_PRIVILEGE_UNIVERSE
--           forecast_role_values                FORECAST_ROLES
--           forecast_relation_privilege_values  FORECAST_ROLE_RELATION_PRIVILEGES
--           forecast_column_privilege_values    FORECAST_ROLE_COLUMN_PRIVILEGES
--           forecast_sequence_privilege_values  FORECAST_ROLE_SEQUENCE_PRIVILEGES
--           forecast_function_privilege_values  FORECAST_ROLE_FUNCTION_PRIVILEGES
--           sequence_privilege_values           SEQUENCE_PRIVILEGE_UNIVERSE
--           required_extension_count            len(REQUIRED_EXTENSIONS)
--           forecast_role_count                 len(FORECAST_ROLES)
--         Because .format() consumes curly braces, any literal brace added to this file
--         later must be DOUBLED or the format call raises KeyError and the whole
--         service fails to import. There are none today.
--
-- What this returns: exactly one row with five boolean columns.
--   extensions_ready         every required PostgreSQL extension is installed
--   migration_catalog_ready  public.alembic_version exists and this login may read it
--   tables_ready             every table the routes touch actually exists
--   privileges_ready         this login holds exactly its contracted privileges, no more
--   forecast_roles_ready     the four forecast capability roles match their contract
-- A false column is a refusal to serve, not an error. The route turns the row into the
-- JSON "checks" object and answers 503. The statement is written so that a missing
-- object can never make it raise -- see coalesce below -- because one absent table must
-- not hide the answers to the other four questions.
--
-- Vocabulary, explained once up front, because every section below reuses it:
--
--   WITH name(column) AS (VALUES ('a'), ('b'))
--     A CTE ("common table expression") is a named subquery defined up front and then
--     referenced below like a table. VALUES writes a literal table straight into the
--     query text. Together they invent a small table -- here a two-row, one-column
--     table called name -- that exists only for this statement. Every expectation in
--     this file arrives that way: the contract lives in Python tuples, gets rendered
--     into these row-literals at import time, and the query then compares what the
--     database actually has against what this build says it should have.
--
--   to_regclass('schema.table') and to_regprocedure('schema.fn(argtypes)')
--     Look an object up by name and return its internal identifier -- its "oid" -- or
--     NULL when no such object exists. Naming a missing table directly would make the
--     whole statement fail to parse, which is precisely the situation a readiness probe
--     has to survive and report on. These two functions turn "does not exist" from a
--     parse error into an ordinary NULL value the query can test.
--
--   has_table_privilege(who, what, 'SELECT')
--     Asks PostgreSQL "may this role do this to this object?" and returns true/false.
--     There is one per object class: has_database_privilege, has_schema_privilege,
--     has_table_privilege, has_column_privilege, has_any_column_privilege,
--     has_sequence_privilege, has_function_privilege. They answer the *effective*
--     question -- privileges reached through role membership count -- which is the
--     question a readiness probe wants, since that is what the connection can really do.
--
--   ... 'SELECT WITH GRANT OPTION'
--     Appending WITH GRANT OPTION to the privilege name asks a different question: may
--     this role hand that privilege on to somebody else? A service login must never be
--     able to. Every privilege assertion in this file is therefore doubled -- hold the
--     privilege, and NOT hold the right to pass it along.
--
--   coalesce(has_table_privilege(...), false)
--     coalesce returns its first non-NULL argument. has_table_privilege returns NULL
--     when the object identifier handed to it is NULL, i.e. when to_regclass found
--     nothing. SQL uses three-valued logic, so an unguarded NULL poisons the AND-chain
--     around it: NULL AND true is NULL, not false, and the whole column would come back
--     NULL instead of answering the question. Wrapping in coalesce makes a missing
--     object read as "no privilege", which is both true and answerable.
--
--   NOT EXISTS (SELECT 1 FROM ... WHERE ...)
--     "No row matches this." Used throughout as a prohibition: each one names a
--     condition that must never be found in the database. SELECT 1 is idiomatic --
--     EXISTS only cares whether any row came back, never what was in it.
--
--   CROSS JOIN
--     Every combination of the rows on the left with the rows on the right, with no
--     join condition. Pairing the objects that exist with the universe of privileges
--     that *could* be granted is how this file asks the "and nothing more" half of its
--     question: it enumerates every privilege that could exist on every object, then
--     requires each one to match the contract exactly. Without it the probe could only
--     confirm that expected grants are present, never that unexpected ones are absent.
--
--   IS DISTINCT FROM
--     Inequality that treats NULL as an ordinary value: NULL IS DISTINCT FROM true is
--     true, whereas NULL <> true is NULL. Comparing "privilege actually held" against
--     "privilege the contract expects" with plain <> would silently pass over every row
--     where either side is NULL. IS DISTINCT FROM catches them.
--
--   aclexplode(acl) and grantee = 0
--     An access control list is stored as one opaque array per object; aclexplode
--     expands it into one row per individual grant. Grantee 0 is the special
--     pseudo-role PUBLIC, meaning "everybody". A grant to PUBLIC is invisible to
--     privilege checks aimed at named roles -- it applies to all of them at once -- so
--     it needs this separate probe of its own.
--
--   acldefault('f', owner)
--     PostgreSQL stores NULL for an access control list nobody has ever modified,
--     meaning "the built-in default for this object class". For functions ('f') that
--     default includes EXECUTE granted to PUBLIC. acldefault reconstructs it so the
--     PUBLIC probe above also sees functions whose ACL was never touched -- inspecting
--     only non-NULL ACLs would miss exactly the untouched, still-public ones.
--
--   pg_auth_members, admin_option, inherit_option
--     pg_auth_members is the role-membership catalog: one row per "member is a member
--     of roleid". admin_option means that member may grant the membership onward;
--     inherit_option means the member picks up the role's privileges automatically
--     rather than having to SET ROLE first. The four forecast roles are capability
--     bundles, so the API login must inherit the reader bundle and must hold no admin
--     option over anything.
--
--   pg_has_role(who, role_oid, 'MEMBER')
--     Effective membership, following chains of nested roles. Used only negatively
--     here: the service login must not reach the database owner, the schema owner, or
--     any object owner, because ownership carries implicit rights that no REVOKE can
--     take away -- an owner can always re-grant itself anything.
--
-- How this query works, section by section:
--
--   required_extensions / required_privileges / publication_tables
--     The contract, as literal tables. required_privileges is one row per
--     (schema, table, privilege) this login must hold; publication_tables collapses it
--     with DISTINCT to the set of tables involved, since several privileges name the
--     same table.
--
--   table_objects CTE
--     Resolves each of those table names to its oid via to_regclass and
--     format('%I.%I', ...). format with %I quotes each identifier the way PostgreSQL
--     would, so a name needing quoting is still looked up correctly. A table that does
--     not exist yields NULL here rather than an error, which is what tables_ready then
--     reports on.
--
--   table_privilege_universe / column_privilege_universe / sequence_privilege_universe
--     The complete set of privileges each object class can carry. These exist purely to
--     be CROSS JOINed against real objects, so the probe can assert the absence of
--     every privilege the contract does not list.
--
--   forecast_role_names / forecast_roles
--     The four expected capability role names, LEFT JOINed to pg_roles. LEFT JOIN keeps
--     the expected name even when no such role exists, leaving role_oid NULL -- that
--     NULL is how "the role is missing entirely" is detected, by counting non-NULL oids
--     against forecast_role_count.
--
--   forecast_relation_privileges / forecast_column_privileges /
--   forecast_sequence_privileges / forecast_function_privileges
--     The full four-role privilege matrix as literal tables: which role may do what to
--     which relation, column, sequence and function.
--
--   agri_relations / agri_relation_columns / forecast_sequences / forecast_functions /
--   agri_sequences / agri_functions
--     What the database actually contains in schema agri, read from the system
--     catalogs. relkind filters pg_class by object kind: 'r' ordinary table,
--     'p' partitioned table, 'v' view, 'm' materialized view, 'f' foreign table,
--     'S' sequence -- pg_class holds them all in one table. attnum > 0 skips the hidden
--     system columns every table has, and NOT attisdropped skips columns that were
--     dropped but whose slots still linger.
--
--   extensions_ready CTE
--     Joins pg_extension to the required list on extname and requires the match count
--     to equal required_extension_count. Counting the join rather than checking each
--     name keeps one comparison for any number of extensions.
--
--   migration_catalog_ready CTE
--     public.alembic_version resolves AND this login may SELECT it. Both halves matter:
--     without the table there is no revision to compare, and without SELECT the probe
--     would fail rather than report. The route only runs the separate revision query
--     when this is true.
--
--   tables_ready CTE
--     bool_and over table_objects -- true only when no resolved oid is NULL, i.e. every
--     contracted table exists. bool_and is the AND-aggregate: it collapses a column of
--     booleans into a single true/false.
--
--   privileges_ready CTE
--     One long AND-chain describing this login exactly. In order: it may CONNECT but
--     not pass CONNECT on and not CREATE; it may USE both schemas but create in
--     neither and pass neither on; it holds every contracted table privilege and the
--     grant option on none of them; it holds no table privilege the contract omits
--     (the CROSS JOIN against table_privilege_universe, filtered by a NOT EXISTS
--     against the contract); and it is an unprivileged login -- not superuser, not
--     createrole, not createdb, not replication, not bypassrls.
--
--   forecast_roles_ready CTE
--     The longest chain, and the one that proves capability separation. All four roles
--     exist; none of them can log in, inherit, or carry any admin attribute; nobody is
--     a member of them except as asserted below; no membership anywhere carries
--     admin_option; each role has exactly CONNECT plus schema USAGE and nothing more;
--     and for relations, columns, sequences and functions alike, the privileges each
--     role actually holds are IS DISTINCT FROM nothing in the contract -- neither
--     missing nor extra -- with no grant option anywhere. Then the PUBLIC probe: no
--     agri function may be executable by everybody. Then this login's own position in
--     that structure: it must be an inheriting, non-admin member of the reader role,
--     and must NOT reach writer, publisher or refresher; it must belong to no other
--     role at all; it must not reach the database, schema, relation, sequence or
--     function owners; it must hold no sequence privilege and no function EXECUTE in
--     agri; and its relation and column privileges must be exactly SELECT on the three
--     serving objects and nothing else. Finally, three existence checks confirm the
--     contract itself still names real objects -- a relation, sequence or function
--     signature that no longer resolves means the contract has drifted from the schema
--     and would otherwise be silently vacuous.
WITH required_extensions(extname) AS (
    VALUES {extension_values}
),
required_privileges(schema_name, table_name, privilege_name) AS (
    VALUES {privilege_values}
),
publication_tables AS (
    SELECT DISTINCT schema_name, table_name FROM required_privileges
),
table_objects AS (
    SELECT
        schema_name,
        table_name,
        to_regclass(format('%I.%I', schema_name, table_name)) AS relation_oid
    FROM publication_tables
),
table_privilege_universe(privilege_name) AS (
    VALUES {table_privilege_values}
),
column_privilege_universe(privilege_name) AS (
    VALUES {column_privilege_values}
),
forecast_role_names(role_name) AS (
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
    WHERE attribute.attnum > 0
      AND NOT attribute.attisdropped
),
forecast_sequences AS (
    SELECT class.oid, class.relname AS sequence_name
    FROM pg_class AS class
    INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = 'agri'
      AND class.relkind = 'S'
),
forecast_functions AS (
    SELECT procedure.oid, procedure.proname, procedure.proacl, procedure.proowner
    FROM pg_proc AS procedure
    INNER JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'agri'
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
    (
        SELECT count(*) = {required_extension_count}
        FROM pg_extension installed
        JOIN required_extensions required USING (extname)
    ) AS extensions_ready,
    (
        to_regclass('public.alembic_version') IS NOT NULL
        AND coalesce(
            has_table_privilege(
                current_user,
                to_regclass('public.alembic_version'),
                'SELECT'
            ),
            false
        )
    ) AS migration_catalog_ready,
    (SELECT bool_and(relation_oid IS NOT NULL) FROM table_objects) AS tables_ready,
    (
        has_database_privilege(current_user, current_database(), 'CONNECT')
        AND NOT has_database_privilege(current_user, current_database(), 'CONNECT WITH GRANT OPTION')
        AND NOT has_database_privilege(current_user, current_database(), 'CREATE')
        AND has_schema_privilege(current_user, 'agri', 'USAGE')
        AND has_schema_privilege(current_user, 'public', 'USAGE')
        AND NOT has_schema_privilege(current_user, 'agri', 'CREATE')
        AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
        AND NOT has_schema_privilege(current_user, 'agri', 'USAGE WITH GRANT OPTION')
        AND NOT has_schema_privilege(current_user, 'public', 'USAGE WITH GRANT OPTION')
        AND (
            SELECT bool_and(
                coalesce(
                    has_table_privilege(
                        current_user,
                        objects.relation_oid,
                        required.privilege_name
                    ),
                    false
                )
                AND NOT coalesce(
                    has_table_privilege(
                        current_user,
                        objects.relation_oid,
                        required.privilege_name || ' WITH GRANT OPTION'
                    ),
                    false
                )
            )
            FROM required_privileges required
            JOIN table_objects objects USING (schema_name, table_name)
        )
        AND NOT EXISTS (
            SELECT 1
            FROM table_objects objects
            CROSS JOIN table_privilege_universe candidate
            WHERE NOT EXISTS (
                SELECT 1
                FROM required_privileges required
                WHERE required.schema_name = objects.schema_name
                  AND required.table_name = objects.table_name
                  AND required.privilege_name = candidate.privilege_name
            )
            AND coalesce(
                has_table_privilege(
                    current_user,
                    objects.relation_oid,
                    candidate.privilege_name
                ),
                false
            )
        )
        AND EXISTS (
            SELECT 1
            FROM pg_roles role
            WHERE role.rolname = current_user
              AND NOT role.rolsuper
              AND NOT role.rolcreaterole
              AND NOT role.rolcreatedb
              AND NOT role.rolreplication
              AND NOT role.rolbypassrls
        )
    ) AS privileges_ready,
    (
        (SELECT count(role_oid) = {forecast_role_count} FROM forecast_roles)
        AND NOT EXISTS (
            SELECT 1
            FROM pg_roles AS role
            INNER JOIN forecast_roles AS expected ON expected.role_oid = role.oid
            WHERE role.rolcanlogin
               OR role.rolinherit
               OR role.rolsuper
               OR role.rolcreatedb
               OR role.rolcreaterole
               OR role.rolreplication
               OR role.rolbypassrls
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
            INNER JOIN forecast_roles AS capability
                ON capability.role_oid = membership.roleid
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
                  (
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
                  (
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
                  (
                      coalesce(
                          has_sequence_privilege(
                              role.role_oid,
                              sequence_object.oid,
                              candidate.privilege_name
                          ),
                          false
                      ) IS DISTINCT FROM EXISTS (
                          SELECT 1
                          FROM forecast_sequence_privileges AS expected
                          WHERE expected.role_name = role.role_name
                            AND expected.schema_name = 'agri'
                            AND expected.sequence_name = sequence_object.sequence_name
                            AND expected.privilege_name = candidate.privilege_name
                      )
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
                  (
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
                FROM aclexplode(
                    coalesce(
                        function_object.proacl,
                        acldefault('f', function_object.proowner)
                    )
                ) AS access
                WHERE access.grantee = 0
                  AND access.privilege_type = 'EXECUTE'
            )
        )
        AND EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            INNER JOIN pg_auth_members AS membership
                ON membership.roleid = role.role_oid
            INNER JOIN pg_roles AS runtime_role
                ON runtime_role.oid = membership.member
            WHERE role.role_name = 'plantgeo_forecast_reader'
              AND runtime_role.rolname = current_user
              AND NOT membership.admin_option
              AND membership.inherit_option
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            WHERE role.role_name = 'plantgeo_forecast_writer'
              AND role.role_oid IS NOT NULL
              AND pg_has_role(current_user, role.role_oid, 'MEMBER')
        )
        AND EXISTS (
            SELECT 1
            FROM pg_roles AS role
            WHERE role.rolname = current_user
              AND NOT role.rolsuper
              AND NOT role.rolcreaterole
              AND NOT role.rolcreatedb
              AND NOT role.rolreplication
              AND NOT role.rolbypassrls
        )
        AND has_database_privilege(current_user, current_database(), 'CONNECT')
        AND NOT has_database_privilege(current_user, current_database(), 'CONNECT WITH GRANT OPTION')
        AND NOT has_database_privilege(current_user, current_database(), 'CREATE')
        AND has_schema_privilege(current_user, 'agri', 'USAGE')
        AND has_schema_privilege(current_user, 'public', 'USAGE')
        AND NOT has_schema_privilege(current_user, 'agri', 'CREATE')
        AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
        AND NOT has_schema_privilege(current_user, 'agri', 'USAGE WITH GRANT OPTION')
        AND NOT has_schema_privilege(current_user, 'public', 'USAGE WITH GRANT OPTION')
        AND NOT coalesce(
            has_table_privilege(
                current_user,
                to_regclass('public.alembic_version'),
                'SELECT WITH GRANT OPTION'
            ),
            false
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            WHERE role.role_name = 'plantgeo_forecast_publisher'
              AND role.role_oid IS NOT NULL
              AND pg_has_role(current_user, role.role_oid, 'MEMBER')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM forecast_roles AS role
            WHERE role.role_name = 'plantgeo_forecast_mv_refresher'
              AND role.role_oid IS NOT NULL
              AND pg_has_role(current_user, role.role_oid, 'MEMBER')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_auth_members AS membership
            INNER JOIN pg_roles AS member_role ON member_role.oid = membership.member
            INNER JOIN pg_roles AS granted_role ON granted_role.oid = membership.roleid
            WHERE member_role.rolname = current_user
              AND granted_role.rolname <> 'plantgeo_forecast_reader'
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
            FROM agri_relations AS relation
            INNER JOIN pg_class AS class ON class.oid = relation.oid
            WHERE pg_has_role(current_user, class.relowner, 'MEMBER')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_functions AS function_object
            WHERE pg_has_role(current_user, function_object.proowner, 'MEMBER')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_sequences AS sequence_object
            WHERE pg_has_role(current_user, sequence_object.relowner, 'MEMBER')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_sequences AS sequence_object
            WHERE has_sequence_privilege(current_user, sequence_object.oid, 'USAGE')
               OR has_sequence_privilege(current_user, sequence_object.oid, 'SELECT')
               OR has_sequence_privilege(current_user, sequence_object.oid, 'UPDATE')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_functions AS function_object
            WHERE has_function_privilege(current_user, function_object.oid, 'EXECUTE')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM agri_relations AS relation
            CROSS JOIN table_privilege_universe AS candidate
            WHERE (
                coalesce(
                    has_table_privilege(current_user, relation.oid, candidate.privilege_name),
                    false
                ) IS DISTINCT FROM (
                    relation.relation_name IN (
                        'v_forecast_series_serving',
                        'mv_forecast_ml_daily_serving',
                        'spatial_cell'
                    )
                    AND candidate.privilege_name = 'SELECT'
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
                AND NOT (
                    relation.relation_name IN (
                        'v_forecast_series_serving',
                        'mv_forecast_ml_daily_serving',
                        'spatial_cell'
                    )
                    AND candidate.privilege_name = 'SELECT'
                  )
            )
            OR has_any_column_privilege(
                current_user,
                relation.oid,
                candidate.privilege_name || ' WITH GRANT OPTION'
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
        )
    ) AS forecast_roles_ready
