# The daily lane-maintenance pass

`cron-maintain-firms` and `cron-maintain-streamflow` are the scheduled half of the
gap-to-backfill loop. Everything else in `infra/cron-*` either FETCHES (`cron-firms`,
`cron-streamflow`, `cron-weather`, ...) or WALKS a planned window (`cron-archive-firms`,
`cron-archive-streamflow`). Nothing scheduled ever asked the ledger to change shape, so a
detected gap and a claimable window were never connected by anything but a person.

**These two services are inert until someone provisions them in Railway.** A `railway.json`
in this repository configures a service that already exists; it does not create one. Until a
Railway service is pointed at this directory, the loop below is code that runs correctly when
invoked by hand and is not running on a schedule. Do not read this file as evidence that it is.

## What each one runs, and in what order

```
agri-cli jobs-plan-gaps      --lane <token> --apply   &&
agri-cli jobs-reconcile-lane --lane <token> --apply
```

Gap planning first, settlement second. Either order converges, and this one is stated by the
task that commissioned it, but it is also the safer read: planning reopens a window over days
that are missing, and the reconcile pass that follows measures that same window, finds the days
still missing, and leaves it queued. The reverse order is not wrong, it just measures a set the
planner is about to change.

`&&` and not `;`. Both verbs exit 0 on "nothing to do" — a lane with no gaps and nothing to
settle is the normal, healthy state — so the only thing that stops the chain is a genuine ledger
failure, which is exactly when the cron run should go red. `restartPolicyType: NEVER` means the
platform will not retry it; the next day's tick is the retry, and neither verb loses anything by
waiting a day.

## The schedule, and why these minutes

`07:17` and `07:47` UTC. Every five-minute-boundary minute (0, 5, 10, ... 55) is already taken by
some `infra/cron-*/railway.json`, so both of these deliberately sit off that grid. The hour is
after `cron-validate`'s `0 6 * * *` completeness report, so an operator reading the morning
report and an operator reading the maintenance log are looking at the same day's warehouse. The
two lanes are half an hour apart so a slow FIRMS pass — the day census over `geo.features` is the
expensive statement here — cannot overlap the streamflow one on the shared warehouse connection.

## What it cannot do, and where that shows up

- **A dead-lettered window stays dead-lettered.** Gap planning reports it and never converts it.
  Requeueing a dead letter is a decision a person makes, because the dead letter is the durable
  evidence that every attempt failed.
- **A cancelled window stays cancelled**, for the same reason: somebody decided that.
- **Days above the newest whole window are never planned.** The forward hourly cron owns the
  present, and a trailing partial window re-keys itself every day. They are reported as
  `unplannable_day_sample` in the verb's JSON line.
- **Nothing here fetches.** These two services only change what the ledger owes; the archive
  walk services (`cron-archive-firms`, `cron-archive-streamflow`) are what actually go and get it,
  so a reopened window sits queued until one of their ticks claims it.
