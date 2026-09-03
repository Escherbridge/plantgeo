"use client";

import { useState } from "react";
import { trpc } from "@/lib/trpc/client";
import { Activity, Play, RefreshCw, Power, Clock, Database, Layers, Info } from "lucide-react";

/**
 * The platform admin view of the durable job runner, over `agri.job_definition` / `agri.job_run`.
 *
 * Everything on this panel is a value the ledger holds. There is no synthetic "records processed",
 * no invented next-run clock, and no cadence editor: nothing in the runtime parses
 * `job_definition.schedule`, so it is shown as declared-and-documentary rather than as a control.
 * A lane appears here once its definition is registered — an empty table means no lane has ever
 * run, which is the honest reading of an empty ledger.
 *
 * The "Governed absences" tab is deliberately styled apart from run history: its rows are windows
 * `jobs-plan-gaps` gave up reopening after five passes, which is evidence the upstream never
 * published those days rather than evidence of a broken fetch. See jobsRouter.getExhaustedGapWindows.
 */

const RUN_STATUS_STYLES: Record<string, string> = {
  queued: "bg-slate-700/40 text-slate-300",
  running: "bg-cyan-500/20 text-cyan-300",
  succeeded: "bg-emerald-500/20 text-emerald-300",
  partial: "bg-amber-500/20 text-amber-300",
  failed: "bg-rose-500/20 text-rose-300",
  dead_letter: "bg-rose-600/30 text-rose-200",
  cancelled: "bg-slate-700/40 text-slate-400",
};

function runStatusClass(status: string | null): string {
  if (!status) return "bg-slate-800 text-slate-500";
  return RUN_STATUS_STYLES[status] ?? "bg-slate-700/40 text-slate-300";
}

/** A bare calendar day, `YYYY-MM-DD`, with no time and no zone. */
const DAY_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Renders an instant, or a DAY as the day it is.
 *
 * The day branch is not cosmetic. The gap-window fields (`first_day`/`last_day`, written by
 * reconcile.py as `date.isoformat()`) are calendar days with no time in them, and `new Date()`
 * parses a bare `YYYY-MM-DD` as UTC MIDNIGHT. Passing that through `toLocaleString()` shifts
 * every operator west of UTC back a day and appends a time nobody measured -- so a census gap
 * on 2026-08-07 read as "8/6/2026, 6:00:00 PM" in Denver, misnaming the very day this console
 * exists to report. Handled here rather than at each call site so no future day-valued column
 * has to remember.
 */
function formatMoment(value: Date | string | null | undefined): string {
  if (!value) return "—";
  if (typeof value === "string" && DAY_ONLY.test(value)) return value;
  const moment = value instanceof Date ? value : new Date(value);
  return Number.isNaN(moment.getTime()) ? "—" : moment.toLocaleString();
}

function formatDuration(
  started: Date | string | null | undefined,
  finished: Date | string | null | undefined
): string {
  if (!started || !finished) return "—";
  const from = new Date(started).getTime();
  const to = new Date(finished).getTime();
  if (Number.isNaN(from) || Number.isNaN(to) || to < from) return "—";
  const seconds = (to - from) / 1000;
  return seconds < 60 ? `${seconds.toFixed(1)}s` : `${(seconds / 60).toFixed(1)}m`;
}

export function JobRunnerDashboard() {
  const [activeTab, setActiveTab] = useState<"lanes" | "history" | "gaps">("lanes");
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const utils = trpc.useUtils();

  const lanes = trpc.jobs.getLanes.useQuery(undefined, { refetchInterval: 15_000 });
  const history = trpc.jobs.getRunHistory.useQuery(
    { limit: 50 },
    { refetchInterval: 15_000, enabled: activeTab === "history" }
  );
  const exhaustedGapWindows = trpc.jobs.getExhaustedGapWindows.useQuery(
    { limit: 50 },
    { refetchInterval: 15_000, enabled: activeTab === "gaps" }
  );

  const invalidate = async () => {
    await Promise.all([
      utils.jobs.getLanes.invalidate(),
      utils.jobs.getRunHistory.invalidate(),
      utils.jobs.getExhaustedGapWindows.invalidate(),
    ]);
  };

  const toggleLane = trpc.jobs.toggleLane.useMutation({
    onSuccess: async (result) => {
      setNotice({
        kind: "ok",
        text: `${result.laneId} is now ${result.enabled ? "enabled" : "paused"} (${result.versionsUpdated} definition row(s) updated).`,
      });
      await invalidate();
    },
    onError: (error) => setNotice({ kind: "error", text: error.message }),
  });

  const triggerLane = trpc.jobs.triggerLane.useMutation({
    onSuccess: async (result) => {
      const slice = result.result;
      setNotice({
        kind: "ok",
        text: slice
          ? `${result.lane_id}: ${slice.stop_reason} — claimed ${slice.claimed}, succeeded ${slice.succeeded}, failed ${slice.dead_lettered}, run ${slice.run_status ?? "unknown"} (${slice.elapsed_seconds}s).`
          : `${result.lane_id}: ${result.state}.`,
      });
      await invalidate();
    },
    // The upstream's own refusal, verbatim: a paused lane, an unknown lane, a ledger error, or an
    // unreachable service each say so here rather than being reported as a successful run.
    onError: (error) => setNotice({ kind: "error", text: error.message }),
  });

  const pending = toggleLane.isPending || triggerLane.isPending;

  if (lanes.isLoading) {
    return (
      <div className="flex items-center justify-center p-12 text-emerald-400">
        <RefreshCw className="w-8 h-8 animate-spin mr-3" />
        <span className="font-medium text-lg">Reading the job ledger…</span>
      </div>
    );
  }

  if (lanes.isError) {
    return (
      <div className="max-w-3xl mx-auto m-6 rounded-2xl border border-rose-500/40 bg-rose-500/10 p-6 text-rose-200">
        <h2 className="font-semibold text-lg">The job ledger could not be read</h2>
        <p className="mt-2 font-mono text-sm">{lanes.error.message}</p>
      </div>
    );
  }

  const rows = lanes.data ?? [];
  const enabledCount = rows.filter((lane) => lane.enabled).length;

  return (
    <div className="w-full max-w-7xl mx-auto p-6 space-y-6 text-slate-100">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-slate-900/80 backdrop-blur-md border border-slate-800 p-6 rounded-2xl shadow-xl">
        <div className="flex items-center space-x-4">
          <div className="p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-xl text-emerald-400">
            <Activity className="w-8 h-8" />
          </div>
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-white">Durable Job Runner</h1>
            <p className="text-slate-400 text-sm mt-1">
              Lanes registered in <code className="font-mono">agri.job_definition</code>, runs from{" "}
              <code className="font-mono">agri.job_run</code>.
            </p>
          </div>
        </div>

        <button
          onClick={() => void invalidate()}
          className="flex items-center space-x-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-medium rounded-xl border border-slate-700 transition"
        >
          <RefreshCw className="w-4 h-4" />
          <span>Refresh</span>
        </button>
      </div>

      {notice && (
        <div
          role="status"
          className={`rounded-xl border p-4 text-sm ${
            notice.kind === "ok"
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
              : "border-rose-500/40 bg-rose-500/10 text-rose-200"
          }`}
        >
          {notice.text}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-slate-900/60 border border-slate-800 p-4 rounded-xl">
          <div className="flex items-center justify-between text-slate-400 text-sm">
            <span>Enabled lanes</span>
            <Layers className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-3xl font-bold text-white mt-2">
            {enabledCount} <span className="text-sm font-normal text-slate-400">/ {rows.length}</span>
          </div>
        </div>

        <div className="bg-slate-900/60 border border-slate-800 p-4 rounded-xl">
          <div className="flex items-center justify-between text-slate-400 text-sm">
            <span>Pause switch</span>
            <Power className="w-4 h-4 text-cyan-400" />
          </div>
          <div className="text-sm font-semibold text-cyan-300 mt-3 font-mono">
            agri.job_definition.enabled
          </div>
        </div>

        <div className="bg-slate-900/60 border border-slate-800 p-4 rounded-xl">
          <div className="flex items-center justify-between text-slate-400 text-sm">
            <span>Work claiming</span>
            <Database className="w-4 h-4 text-indigo-400" />
          </div>
          <div className="text-sm font-semibold text-indigo-300 mt-3 font-mono">
            FOR UPDATE SKIP LOCKED + lease fence
          </div>
        </div>
      </div>

      <div className="flex space-x-2 border-b border-slate-800 pb-2">
        <button
          onClick={() => setActiveTab("lanes")}
          className={`px-4 py-2 rounded-xl text-sm font-medium transition ${
            activeTab === "lanes"
              ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          Lanes
        </button>
        <button
          onClick={() => setActiveTab("history")}
          className={`px-4 py-2 rounded-xl text-sm font-medium transition ${
            activeTab === "history"
              ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          Run history
        </button>
        <button
          onClick={() => setActiveTab("gaps")}
          className={`px-4 py-2 rounded-xl text-sm font-medium transition ${
            activeTab === "gaps"
              ? "bg-indigo-500/20 text-indigo-300 border border-indigo-500/30"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          Governed absences
        </button>
      </div>

      {activeTab === "lanes" ? (
        rows.length === 0 ? (
          <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-8 text-center text-slate-400">
            No lane has registered a definition yet. A lane appears here the first time the
            agri-data-service upserts it — nothing is listed speculatively.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {rows.map((lane) => (
              <div
                key={lane.laneId}
                className="bg-slate-900/80 border border-slate-800 hover:border-slate-700 p-6 rounded-2xl transition shadow-lg flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-lg font-bold text-white tracking-wide break-all">
                      {lane.laneId}
                    </span>
                    <button
                      onClick={() =>
                        toggleLane.mutate({ laneId: lane.laneId, enabled: !lane.enabled })
                      }
                      disabled={pending}
                      className={`p-2 rounded-xl border transition disabled:opacity-50 ${
                        lane.enabled
                          ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/30"
                          : "bg-slate-800 border-slate-700 text-slate-500 hover:bg-slate-700"
                      }`}
                      title={lane.enabled ? "Pause this lane" : "Enable this lane"}
                      aria-label={lane.enabled ? `Pause ${lane.laneId}` : `Enable ${lane.laneId}`}
                    >
                      <Power className="w-5 h-5" />
                    </button>
                  </div>

                  <div className="mt-4 space-y-2 text-sm text-slate-400">
                    <div className="flex justify-between gap-4">
                      <span>Handler</span>
                      <span className="font-mono text-slate-200 text-xs break-all text-right">
                        {lane.handler}
                      </span>
                    </div>
                    <div className="flex justify-between gap-4">
                      <span>Version</span>
                      <span className="text-slate-200">
                        {lane.version}
                        {lane.versionCount > 1 && (
                          <span className="text-slate-500"> ({lane.versionCount} registered)</span>
                        )}
                      </span>
                    </div>
                    <div className="flex justify-between gap-4">
                      <span title="Stored on the definition for operators; no scheduler parses it.">
                        Declared cadence
                      </span>
                      <span className="font-mono text-slate-200">
                        {lane.scheduleCron ?? "none"}
                      </span>
                    </div>
                    <div className="flex justify-between gap-4">
                      <span>Slice budget / lease</span>
                      <span className="text-slate-200">
                        {lane.timeBudgetSeconds}s / {lane.leaseSeconds}s
                      </span>
                    </div>
                    <div className="flex justify-between gap-4">
                      <span>State</span>
                      <span
                        className={`font-semibold ${
                          lane.enabled ? "text-emerald-400" : "text-amber-400"
                        }`}
                      >
                        {lane.enabled ? "enabled" : "paused"}
                      </span>
                    </div>
                  </div>

                  <div className="mt-4 pt-4 border-t border-slate-800/80 text-sm">
                    <div className="text-slate-400 mb-2 flex items-center gap-2">
                      <Clock className="w-3.5 h-3.5" />
                      Last run
                    </div>
                    {lane.lastRun === null ? (
                      <p className="text-slate-500">Never run.</p>
                    ) : (
                      <div className="space-y-1 text-xs">
                        <div className="flex items-center gap-2">
                          <span
                            className={`px-2 py-0.5 rounded font-semibold ${runStatusClass(lane.lastRun.status)}`}
                          >
                            {lane.lastRun.status ?? "unknown"}
                          </span>
                          <span className="text-slate-400">
                            {lane.lastRun.succeededWorkItems}/{lane.lastRun.totalWorkItems} items
                            {lane.lastRun.failedWorkItems ? `, ${lane.lastRun.failedWorkItems} failed` : ""}
                          </span>
                        </div>
                        <div className="text-slate-500">
                          started {formatMoment(lane.lastRun.startedAt)} · finished{" "}
                          {formatMoment(lane.lastRun.completedAt)}
                        </div>
                        {lane.lastRun.error && (
                          <p className="text-rose-300 wrap-break-word">{lane.lastRun.error}</p>
                        )}
                      </div>
                    )}
                  </div>
                </div>

                <div className="mt-6 pt-4 border-t border-slate-800/80 flex items-center justify-end">
                  <button
                    onClick={() => triggerLane.mutate({ laneId: lane.laneId })}
                    disabled={pending}
                    className="flex items-center space-x-2 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-xs font-semibold rounded-lg transition"
                  >
                    {triggerLane.isPending ? (
                      <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Play className="w-3.5 h-3.5 fill-current" />
                    )}
                    <span>RUN NOW</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )
      ) : activeTab === "history" ? (
        <div className="bg-slate-900/80 border border-slate-800 rounded-2xl overflow-x-auto shadow-xl">
          {history.isLoading ? (
            <p className="p-6 text-slate-400">Reading run history…</p>
          ) : history.isError ? (
            <p className="p-6 text-rose-300 font-mono text-sm">{history.error.message}</p>
          ) : (history.data ?? []).length === 0 ? (
            <p className="p-6 text-slate-400">
              No runs recorded. Every row of this table is an <code className="font-mono">agri.job_run</code>{" "}
              row; an empty table means no lane has executed.
            </p>
          ) : (
            <table className="w-full text-left text-sm text-slate-300">
              <thead className="bg-slate-800/60 text-slate-400 font-mono text-xs uppercase border-b border-slate-800">
                <tr>
                  <th className="p-4">Lane</th>
                  <th className="p-4">Status</th>
                  <th className="p-4">Items (ok / failed / total)</th>
                  <th className="p-4">Requested by</th>
                  <th className="p-4">Started</th>
                  <th className="p-4">Finished</th>
                  <th className="p-4">Duration</th>
                  <th className="p-4">Error</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {(history.data ?? []).map((run) => (
                  <tr key={run.runId} className="hover:bg-slate-800/40 align-top">
                    <td className="p-4 font-mono font-medium text-white break-all">
                      {run.laneId}
                      <div className="text-[11px] text-slate-500 font-normal break-all">
                        {run.runKey}
                      </div>
                    </td>
                    <td className="p-4">
                      <span className={`px-2 py-0.5 rounded text-xs font-semibold ${runStatusClass(run.status)}`}>
                        {run.status}
                      </span>
                    </td>
                    <td className="p-4 font-mono text-xs">
                      {run.succeededWorkItems} / {run.failedWorkItems} / {run.totalWorkItems}
                    </td>
                    <td className="p-4 text-xs text-slate-400">{run.requestedBy ?? "—"}</td>
                    <td className="p-4 text-xs text-slate-400">{formatMoment(run.startedAt)}</td>
                    <td className="p-4 text-xs text-slate-400">{formatMoment(run.completedAt)}</td>
                    <td className="p-4 text-xs text-slate-400">
                      {formatDuration(run.startedAt, run.completedAt)}
                    </td>
                    <td className="p-4 text-xs text-rose-300 max-w-xs wrap-break-word">
                      {run.error ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <div className="rounded-2xl border border-indigo-500/30 bg-indigo-500/10 p-5 flex gap-3">
            <Info className="w-5 h-5 text-indigo-300 shrink-0 mt-0.5" />
            <div className="text-sm text-indigo-100/90 leading-relaxed">
              <p className="font-semibold text-indigo-200">
                These windows are evidence of governed absence, not failures.
              </p>
              <p className="mt-1 text-indigo-100/80">
                <code className="font-mono text-xs">jobs-plan-gaps</code> reopens a succeeded window
                whenever the days it claimed to cover still show up missing — but only up to five
                times. Past that, a day the upstream still doesn&apos;t serve is read as proof the
                source never published anything for it (an off-season for a satellite pass, a sensor
                with no coverage there, a publication cadence the layer just hasn&apos;t reached yet),
                not as a fetch that keeps breaking. Rather than reopen a sixth time, the ledger reports
                the window here and leaves it alone.
              </p>
            </div>
          </div>

          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl overflow-x-auto shadow-xl">
            {exhaustedGapWindows.isLoading ? (
              <p className="p-6 text-slate-400">Reading reopen-exhausted windows…</p>
            ) : exhaustedGapWindows.isError ? (
              <p className="p-6 text-rose-300 font-mono text-sm">{exhaustedGapWindows.error.message}</p>
            ) : (exhaustedGapWindows.data ?? []).length === 0 ? (
              <p className="p-6 text-slate-400">
                No window has exhausted its reopens. Every gap{" "}
                <code className="font-mono">jobs-plan-gaps</code> has found so far has either closed on
                a later pass or is still within its five reopen attempts.
              </p>
            ) : (
              <table className="w-full text-left text-sm text-slate-300">
                <thead className="bg-slate-800/60 text-slate-400 font-mono text-xs uppercase border-b border-slate-800">
                  <tr>
                    <th className="p-4">Lane</th>
                    <th className="p-4">Layer</th>
                    <th className="p-4">Window span</th>
                    <th className="p-4">Missing days</th>
                    <th className="p-4">First → last missing</th>
                    <th className="p-4">Reopen passes</th>
                    <th className="p-4">Last reopened</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {(exhaustedGapWindows.data ?? []).map((gapWindow, index) => (
                    /* `shard_key` is unique per (job_run_id, shard_key) only -- see
                       uq_job_work_item_run_shard -- and getExhaustedGapWindows spans every run,
                       so the same lane re-walking a window yields the key twice. React would
                       then drop a row. The lane and generation disambiguate the repeats this
                       query can actually produce; the index closes the shape entirely. */
                    <tr
                      key={`${gapWindow.laneId}:${gapWindow.shardKey}:${gapWindow.walkGeneration}:${index}`}
                      className="hover:bg-slate-800/40 align-top"
                    >
                      <td className="p-4 font-mono font-medium text-white break-all">
                        {gapWindow.laneId}
                        <div className="text-[11px] text-slate-500 font-normal break-all">
                          {gapWindow.shardKey}
                        </div>
                      </td>
                      <td className="p-4 text-xs text-slate-400 font-mono">
                        {gapWindow.layerReference ?? "—"}
                      </td>
                      <td className="p-4 text-xs text-slate-400">
                        {formatMoment(gapWindow.windowStart)} – {formatMoment(gapWindow.windowEnd)}
                      </td>
                      <td className="p-4">
                        <span className="px-2 py-0.5 rounded text-xs font-semibold bg-indigo-500/20 text-indigo-300">
                          {gapWindow.missingDayCount} day{gapWindow.missingDayCount === 1 ? "" : "s"}
                        </span>
                      </td>
                      <td className="p-4 text-xs text-slate-400">
                        {formatMoment(gapWindow.firstMissingDay)} → {formatMoment(gapWindow.lastMissingDay)}
                      </td>
                      <td className="p-4 text-xs text-slate-400">{gapWindow.walkGeneration}</td>
                      <td className="p-4 text-xs text-slate-400">{formatMoment(gapWindow.reopenedAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
