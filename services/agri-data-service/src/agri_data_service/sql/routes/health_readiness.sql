-- Purpose: the single probe /ready runs against whichever production database profile
--          this process was started with. It answers two independent yes/no questions
--          about the database it just connected to -- are the extensions this build needs
--          installed, and is the migration catalog reachable -- and returns them as two
--          boolean columns of one row.
-- Loaded by: agri_data_service.routes.health.queries
-- Params: none. This statement binds no parameters at all; the one expectation it compares
--         the live database against is baked into the text at import time from a Python
--         tuple (see Placeholder below).
--
-- Parameter and slot names appear in these comments WITHOUT a leading colon. See
-- "Header/bind-param trap" in sql/AGENTS.md: SQLAlchemy scans comments for
-- colon-prefixed words as well as real SQL, and a name written with its colon here
-- would mint a bind parameter no caller supplies, failing every execution.
--
-- Placeholder: this file is .format()ted once, at module import time, before it is
--         handed to text(). The named slots below -- written in curly braces in the
--         VALUES list and in the count comparison -- are filled from Python constants in
--         agri_data_service.routes.health.contracts, rendered into a SQL row-literal by
--         _sql_values in agri_data_service.routes.health.queries. Neither is ever request
--         input; they are this build's hard-coded readiness contract, which is what makes
--         baking them into the statement text legal rather than a SQL-injection surface.
--         The slots are:
--           extension_values          REQUIRED_EXTENSIONS
--           required_extension_count  len(REQUIRED_EXTENSIONS)
--         Because .format() consumes curly braces, any literal brace added to this file
--         later must be DOUBLED or the format call raises KeyError and the whole
--         service fails to import. There are none today.
--
-- What this statement no longer asks, and why. Until revision 20260808_0019 this file also
-- returned tables_ready, privileges_ready and forecast_roles_ready: a full privilege matrix
-- for the four forecast capability roles and for the calling login. The owner
-- retired that family (zero members, no DSN, and no USAGE on schema agri since 20260803_0018
-- orphaned them), and every application now connects with the single owner credential. An
-- "exactly these privileges and no more" assertion about an owner is vacuous, so the sections
-- that made it were deleted rather than weakened into something that always passes. Schema
-- governance is unaffected: the pinned Alembic revision is still asserted, by
-- health_migration.sql, which the route runs only when migration_catalog_ready below is true.
--
-- What this returns: exactly one row with two boolean columns.
--   extensions_ready         every required PostgreSQL extension is installed
--   migration_catalog_ready  public.alembic_version exists and this login may read it
-- A false column is a refusal to serve, not an error. The route turns the row into the
-- JSON "checks" object and answers 503. The statement is written so that a missing
-- object can never make it raise -- see coalesce below -- because one absent table must
-- not hide the answer to the other question.
--
-- How this query works, section by section:
--
--   WITH required_extensions(extname) AS (VALUES ('postgis'), ('vector'), ('pgcrypto'))
--     A CTE ("common table expression") is a named subquery defined up front and then
--     referenced below like a table. VALUES writes a literal table straight into the
--     query text. Together they invent a small table -- here a three-row, one-column
--     table called required_extensions -- that exists only for this statement. The
--     contract lives in a Python tuple, gets rendered into that row-literal at import
--     time, and the query then compares what the database actually has against what
--     this build says it should have.
--
--   The extensions_ready column
--     Joins pg_extension (PostgreSQL's catalog of installed extensions) to the required
--     list on extname and requires the match count to equal required_extension_count.
--     Counting the join rather than checking each name keeps one comparison for any
--     number of extensions. USING (extname) is shorthand for "join on the column both
--     sides call extname"; an extension the database has but the contract does not name
--     simply does not join, so extra extensions are not a readiness failure.
--
--   The serving_surface_ready column
--     The one capability question kept after the role teardown, and it is deliberately
--     login-agnostic: has_schema_privilege asks "may whoever is connected right now see
--     schema agri at all", and to_regclass asks "does the serving view still exist".
--     Neither is vacuous for an owner -- an owner without USAGE on a schema it does not
--     own still fails the first half -- and together they catch exactly the failure the
--     retired role matrix used to catch in one line: a login that can reach the database
--     and read the migration catalog but would 500 on its first real request. No role is
--     named; the question is about capability, not identity.
--
--   The migration_catalog_ready column
--     Two halves, both required. to_regclass('public.alembic_version') looks the table up
--     by name and returns its internal identifier -- its "oid" -- or NULL when no such
--     table exists; naming a missing table directly would make the whole statement fail
--     to parse, which is exactly the situation a readiness probe has to survive and
--     report on. has_table_privilege then asks PostgreSQL "may this role SELECT from
--     this?" and returns true/false. coalesce returns its first non-NULL argument, and it
--     is needed because has_table_privilege returns NULL when the identifier handed to it
--     is NULL: SQL uses three-valued logic, so an unguarded NULL would poison the AND
--     around it (NULL AND true is NULL, not false) and the column would come back NULL
--     instead of answering the question. Wrapping in coalesce makes a missing table read
--     as "no privilege", which is both true and answerable. Both halves matter: without
--     the table there is no revision to compare, and without SELECT the separate revision
--     query would fail rather than report, which is why the route runs that query only
--     when this column is true.
WITH required_extensions(extname) AS (
    VALUES {extension_values}
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
    (
        has_schema_privilege(current_user, 'agri', 'USAGE')
        AND to_regclass('agri.v_forecast_series_serving') IS NOT NULL
    ) AS serving_surface_ready
