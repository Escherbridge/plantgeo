export const meta = {
  name: 'validate-data-streams',
  description: 'Run the cross-stream completeness + validity report against the warehouse and interpret its findings',
  whenToUse:
    'Before or after any backfill, ingest change or deploy that touches the warehouse. Answers ' +
    '"which streams are complete, which are missing data, and which are serving something invalid" ' +
    'with evidence, and writes a dated markdown report to docs/reports/.',
  phases: [
    { title: 'Report', detail: 'run validate-streams and jobs-status against the warehouse' },
    { title: 'Interpret', detail: 'one investigator per non-complete stream' },
    { title: 'Synthesise', detail: 'a single ranked findings document' },
  ],
}

// The engine is `agri-cli validate-streams`, not this script. The verb owns the SQL, the
// continuity rules mirrored from the UI's read model, and the verdicts; this workflow exists to
// RUN it, then spend one agent per problem stream working out WHY -- which is the part a report
// cannot do for itself. Keeping the rules in the verb is what lets a Railway cron run the same
// check unattended (infra/cron-validate) and get the same answer.
//
// See docs/runbooks/durable-backfill-lanes.md for the lane model the lane findings refer to.

const SERVICE_DIRECTORY = 'services/agri-data-service'

// The verb reaches the warehouse through ingest_session(), which requires
// LOCAL_SOURCE_LOADER_DATABASE_URL to carry the real DSN while DATABASE_URL holds a different,
// unroutable value -- Settings refuses them being equal. run-backfill.sh already establishes
// exactly that contract, so it is invoked rather than re-implemented.
const RUN = `cd ${SERVICE_DIRECTORY} && ./run-backfill.sh`

const reportDate = (args && args.reportDate) || 'undated'
const reportPath = `docs/reports/data-stream-validation-${reportDate}.md`

phase('Report')

const report = await agent(
  `Run the warehouse validation report and return its raw findings. Make no code changes.

From the repository root:
  ${RUN} validate-streams --format json
  ${RUN} validate-streams --format markdown --output ${reportPath}
  ${RUN} jobs-status

Exit codes are meaningful and must not be "fixed": validate-streams exits 1 when at least one
stream is INVALID, and exits 0 when streams are merely INCOMPLETE. An in-flight backfill is
incomplete by definition, so incomplete is not a failure.

Never print the DSN or its password.

Return, verbatim and complete:
1. The per-stream table: stream, rows, first day, last day, distinct days, missing days,
   worst gap, verdict.
2. Every non-zero validity finding with its count and which stream it belongs to.
3. The lane state from jobs-status: per definition, counts by work item state, the oldest
   outstanding window, and every dead-lettered shard key.
4. The exit code of each command.
5. Anything the report itself flags as not evaluated (for example, a check skipped because
   INGEST_BBOX was unset) -- a check with no coverage must not read as a check that passed.`,
  { label: 'validate:run', phase: 'Report' },
)

phase('Interpret')

// One investigator per problem stream. The report says WHAT is missing; only a look at the
// producer says why -- and the answer differs in kind: an upstream that publishes weekly is not
// a gap, a lane holding dead-lettered windows is, and a sentinel written as a reading is a live
// bug. Fanned out because the investigations are independent and each is a different producer.
const investigations = await agent(
  `From the validation output below, list every stream whose verdict is INCOMPLETE or INVALID.

${report}

Return ONLY a JSON array of objects: [{"stream": "...", "verdict": "...", "headline": "..."}]
where headline is the single most important number for that stream (largest gap, sentinel
count, dead-lettered window count). No prose, no code fences.`,
  { label: 'validate:triage', phase: 'Interpret', model: 'haiku' },
)

let problemStreams
try {
  problemStreams = JSON.parse(investigations.trim().replace(/^```(?:json)?|```$/g, ''))
} catch {
  problemStreams = []
  log('Could not parse the triage list; falling back to a single combined investigation.')
}

const findings = problemStreams.length
  ? await pipeline(
      problemStreams.slice(0, 8),
      (stream) =>
        agent(
          `Investigate ONE warehouse stream that the validation report did not pass. Read-only:
change no code, run no ingestion, write no files.

Stream: ${stream.stream}
Verdict: ${stream.verdict}
Headline: ${stream.headline}

Full report context:
${report}

Work out WHY, and distinguish the three cases that look identical in a gap count:
  a) EXPECTED -- the upstream's own publication cadence (USDM is weekly; Sentinel-2 revisits
     every ~5 days; a reference layer has no cadence at all). Not a defect. Say so plainly.
  b) MISSING WORK -- days the producer should have and does not. Name the exact day ranges,
     say whether a backfill lane covers them, and whether that lane has dead-lettered windows.
  c) A BUG -- something is writing wrong data, or a producer is silently failing. Name the
     file and function.

Read the producer: ${SERVICE_DIRECTORY}/src/agri_data_service/ingest/ has one module per source,
and ingest/AGENTS.md carries the rationale for each. ingest/lanes.py declares which streams have
an archive lane and the floor each one walks from.

You may query the warehouse read-only to confirm a hypothesis. Never print the DSN.

Return: the classification (a, b or c), the evidence that decided it, the exact affected day
ranges, and -- only if it is (b) or (c) -- the specific next action, naming the CLI verb or the
file to change. If it is (a), say "no action" and stop.`,
          { label: `investigate:${stream.stream}`, phase: 'Interpret' },
        ),
    )
  : []

phase('Synthesise')

const synthesis = await agent(
  `Write the operator-facing summary of this warehouse validation run.

## The report
${report}

## Per-stream investigations
${findings.filter(Boolean).join('\n\n---\n\n')}

Produce a single markdown document:

1. **Verdict line** -- how many streams are complete, incomplete and invalid, in one sentence.
2. **Act on these** -- only findings classified as MISSING WORK or A BUG, ranked by how much
   data is at stake, each with its exact next action (a CLI verb to run, or a file to change).
3. **Expected, not a defect** -- streams whose gaps are their upstream's own cadence, one line
   each, so nobody re-investigates them next time.
4. **Not evaluated** -- every check that had no coverage this run and why. A check that did not
   run must never be reported as a check that passed.
5. **Lane state** -- per lane: outstanding windows, dead-lettered windows, oldest outstanding.
   A lane with any dead-lettered window is not complete regardless of what its stream's
   coverage looks like.

Be specific and quantitative. Never write "looks good" where a number belongs. If two findings
have the same root cause, say so once rather than twice.

The full machine-readable report is at ${reportPath}; reference it rather than restating it.`,
  { label: 'validate:synthesise', phase: 'Synthesise', model: 'opus' },
)

return { reportPath, synthesis }
