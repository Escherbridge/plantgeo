-- Purpose: one row per AGGREGATE platform-activity metric -- how many of a thing exist, how many
--          appeared in the last day and the last week, and when the newest one appeared -- so
--          /ops/backfill can show whether anyone is using the platform the pipelines feed.
-- Loaded by: agri_data_service.routes.ops
-- Params: none.
--
-- Parameter names would appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and would mint a bind
-- parameter nobody supplies. This file binds nothing.
--
-- COUNTS AND TIMESTAMPS ONLY. THIS IS A HARD SCOPING RULE, NOT A STYLE CHOICE.
--
-- /ops/backfill is an UNAUTHENTICATED page. Owner decision: this statement returns aggregate
-- counts and aggregate timestamps and NOTHING ELSE. No email, no name, no team name, no slug, no
-- user id, no IP, no latitude or longitude, no "most active organization", no recent-activity
-- list -- nothing that identifies a person or an organization, and nothing that could be joined
-- back to one. Several of the tables read below carry exactly those columns (public.users has
-- email and name, public.teams has name and slug, public.ai_conversations, public.watched_locations
-- and public.strategy_requests all carry lat/lon). None of them appear in any SELECT list here,
-- and none of them may be added. If a future panel needs per-user or per-organization detail, it
-- needs an authenticated route first -- not a column added to this file.
--
-- WHY THIS STATEMENT EXISTS
--
-- Every other panel on this page watches data going IN. Nothing watched whether anything comes
-- OUT: an operator could see forty million observations landing and have no way to tell that the
-- product on top of them has one account, one organization, zero sessions and zero conversations.
-- That is not a criticism of the pipelines, it is the other half of the health picture, and it
-- was invisible.
--
-- WHAT THIS DATABASE CANNOT MEASURE, STATED PLAINLY
--
-- THERE IS NO REQUEST LOG, ACCESS LOG OR AUDIT TABLE ANYWHERE IN THIS DATABASE. Verified
-- 2026-08-09. Nothing here is a request-volume metric and nothing below should be read as one.
-- The two closest honest proxies both have caveats and both are on the page with their basis
-- named:
--   * public.ai_messages.created_at -- assistant traffic only. A user browsing the map all day
--     writes nothing here.
--   * public.api_keys.last_used -- programmatic access only, and it is a LAST-USE stamp, not a
--     creation stamp, so its trailing windows count keys USED in the window rather than keys
--     issued in it. That is why window_basis travels with every row.
-- A panel that silently omitted request volume would let a reader assume the number simply was
-- not interesting; saying the platform does not record it is the only honest option.
--
-- public.sessions IS A SPECIAL CASE and its row is deliberately different from every other one.
-- The table holds exactly three columns -- session_token, user_id, expires -- and has NO creation
-- timestamp at all (verified against production 2026-08-09). "Active sessions" therefore cannot
-- mean "started recently"; the only thing the schema can answer is "not yet expired", which is
-- what its row counts. Its trailing windows and its last-activity stamp are NULL rather than
-- zero, because zero would claim nobody signed in this week and the truth is that this database
-- cannot tell.
--
-- public.users HAS TWO VERIFICATION COLUMNS AND ONLY ONE OF THEM IS EMAIL VERIFICATION.
--
-- `verified boolean` and `email_verified timestamptz` both exist on that table. The application
-- verifies an email address by stamping `email_verified` and NEVER writes `verified`
-- (src/lib/server/auth-options.ts, src/app/api/auth/verify-email/route.ts,
-- src/lib/server/security/invitations.ts, src/lib/server/trpc/routers/teams.ts). `verified` is an
-- unrelated boolean that defaults to false and no auth path touches.
--
-- The verified-accounts row below therefore tests `email_verified IS NOT NULL`. Reading `verified`
-- instead produces the right answer today purely by accident -- both columns are falsy on the one
-- account that exists -- and becomes permanently wrong the moment anybody verifies an address:
-- `email_verified` gets stamped, `verified` stays false, and this console reports "0 verified of 1"
-- forever. A confidently labelled wrong number is the exact failure this page exists to prevent,
-- so the column is named here rather than left to be inferred from the row's label.
--
-- WHY EVERY TIMESTAMP IS TRUNCATED TO THE HOUR
--
-- date_trunc('hour', ...) on every max() below. These are aggregates and none of them names
-- anybody, but the platform currently has ONE account and ONE organization, and at a population of
-- one an "aggregate" newest-created stamp is that single subject's registration instant to the
-- microsecond. The owner's rule permits timestamps; its spirit is that nothing on this page
-- describes an individual. An hour costs this panel nothing -- it exists to answer "is anyone
-- using this", and no operational question here turns on a sub-hour difference -- so the precision
-- that has no use here is simply not published.
--
-- WHY TRAILING WINDOWS AND NOT JUST TOTALS
--
-- A total is a static number: one account and one organization read exactly the same on a
-- platform that launched yesterday and one abandoned a year ago. The 24-hour and 7-day columns
-- are what make this a liveness signal rather than a plaque, and they are what will show the
-- first real user arriving without anyone editing this file.
--
-- How this query works, clause by clause:
--
--   UNION ALL
--     Stacks each metric's one-row result into a single result set. ALL rather than plain UNION
--     because UNION would de-duplicate identical rows -- and two different metrics that happen to
--     hold the same counts really are two rows, not one. Every branch must return the same columns
--     in the same order; the first branch's casts fix the type of each column for all of them.
--
--   count(*) FILTER (WHERE created_at >= now() - interval '24 hours')
--     A filtered aggregate: it counts only the rows matching its own condition, while the
--     unfiltered count(*) beside it counts them all. This is what lets one scan of one table
--     answer "how many exist", "how many in a day" and "how many in a week" at once, instead of
--     three separate passes. now() is the current transaction timestamp, so every window in every
--     branch is measured from the same instant.
--
--   count(*) FILTER (WHERE account.email_verified IS NOT NULL) as a separate row
--     Verified accounts are a SUBSET of registered accounts, not a different table, so this row
--     reads the same table with the subset condition applied to its total and to both windows.
--     Rendering it as its own row rather than an extra column keeps every row in this result the
--     same shape. IS NOT NULL rather than a boolean test because the column that records email
--     verification is a nullable timestamp -- see the two-verification-columns note above, which is
--     the difference between this row being right and it reading zero forever.
--
--   date_trunc('hour', max(...))
--     Rounds a timestamp down to the top of its hour. Applied to every aggregate stamp in this
--     statement for the reason given above: at a population of one, microsecond precision on a
--     "newest" stamp is per-subject data that this panel has no use for.
--
--   NULL::bigint in the sessions branch
--     Not zero. See the sessions note above: an unmeasurable quantity must be absent, and the
--     route renders NULL as an em dash beside the reason. The cast is needed because the branch
--     would otherwise contribute an untyped NULL to a column the first branch typed as bigint.
--
--   max(...) as last_activity_at
--     The newest stamp of whatever window_basis names for that row. It is an aggregate over the
--     whole table and identifies nobody: it says when the most recent thing of this kind happened,
--     never which one or whose.
--
--   ORDER BY metric_ordinal
--     A total order, so the panel's rows never shuffle between refreshes. The ordinal also carries
--     the reading order of the sections, which alphabetical order would scramble.

select
    1 as metric_ordinal,
    'accounts' as section,
    'registered accounts' as metric,
    'public.users' as relation,
    count(*)::bigint as total_count,
    count(*) filter (where account.created_at >= now() - interval '24 hours')::bigint as last_24h,
    count(*) filter (where account.created_at >= now() - interval '7 days')::bigint as last_7d,
    date_trunc('hour', max(account.created_at))::timestamptz as last_activity_at,
    'created_at'::text as window_basis
from public.users as account

union all

select
    2,
    'accounts',
    'accounts with a verified email',
    'public.users',
    count(*) filter (where account.email_verified is not null),
    count(*) filter (where account.email_verified is not null and account.created_at >= now() - interval '24 hours'),
    count(*) filter (where account.email_verified is not null and account.created_at >= now() - interval '7 days'),
    date_trunc('hour', max(account.created_at) filter (where account.email_verified is not null)),
    'created_at'
from public.users as account

union all

select
    3,
    'accounts',
    'organizations',
    'public.teams',
    count(*),
    count(*) filter (where team.created_at >= now() - interval '24 hours'),
    count(*) filter (where team.created_at >= now() - interval '7 days'),
    date_trunc('hour', max(team.created_at)),
    'created_at'
from public.teams as team

union all

select
    4,
    'accounts',
    'organization memberships',
    'public.team_members',
    count(*),
    count(*) filter (where membership.joined_at >= now() - interval '24 hours'),
    count(*) filter (where membership.joined_at >= now() - interval '7 days'),
    date_trunc('hour', max(membership.joined_at)),
    'joined_at'
from public.team_members as membership

union all

-- The one row whose windows are NULL by construction; see the sessions note in the header.
select
    5,
    'access',
    'unexpired sessions',
    'public.sessions',
    count(*) filter (where session.expires > now()),
    null::bigint,
    null::bigint,
    null::timestamptz,
    null::text
from public.sessions as session

union all

select
    6,
    'access',
    'api keys issued',
    'public.api_keys',
    count(*),
    count(*) filter (where api_key.last_used >= now() - interval '24 hours'),
    count(*) filter (where api_key.last_used >= now() - interval '7 days'),
    date_trunc('hour', max(api_key.last_used)),
    'last_used'
from public.api_keys as api_key

union all

select
    7,
    'assistant',
    'ai conversations',
    'public.ai_conversations',
    count(*),
    count(*) filter (where conversation.created_at >= now() - interval '24 hours'),
    count(*) filter (where conversation.created_at >= now() - interval '7 days'),
    date_trunc('hour', max(conversation.created_at)),
    'created_at'
from public.ai_conversations as conversation

union all

select
    8,
    'assistant',
    'ai messages',
    'public.ai_messages',
    count(*),
    count(*) filter (where message.created_at >= now() - interval '24 hours'),
    count(*) filter (where message.created_at >= now() - interval '7 days'),
    date_trunc('hour', max(message.created_at)),
    'created_at'
from public.ai_messages as message

union all

select
    9,
    'watches and alerts',
    'watched locations',
    'public.watched_locations',
    count(*),
    count(*) filter (where watch.created_at >= now() - interval '24 hours'),
    count(*) filter (where watch.created_at >= now() - interval '7 days'),
    date_trunc('hour', max(watch.created_at)),
    'created_at'
from public.watched_locations as watch

union all

select
    10,
    'watches and alerts',
    'alert subscriptions',
    'public.alert_subscriptions',
    count(*),
    count(*) filter (where subscription.created_at >= now() - interval '24 hours'),
    count(*) filter (where subscription.created_at >= now() - interval '7 days'),
    date_trunc('hour', max(subscription.created_at)),
    'created_at'
from public.alert_subscriptions as subscription

union all

select
    11,
    'watches and alerts',
    'environmental alerts raised',
    'public.environmental_alerts',
    count(*),
    count(*) filter (where alert.created_at >= now() - interval '24 hours'),
    count(*) filter (where alert.created_at >= now() - interval '7 days'),
    date_trunc('hour', max(alert.created_at)),
    'created_at'
from public.environmental_alerts as alert

union all

select
    12,
    'requests',
    'strategy requests',
    'public.strategy_requests',
    count(*),
    count(*) filter (where strategy_request.created_at >= now() - interval '24 hours'),
    count(*) filter (where strategy_request.created_at >= now() - interval '7 days'),
    date_trunc('hour', max(strategy_request.created_at)),
    'created_at'
from public.strategy_requests as strategy_request

order by metric_ordinal
