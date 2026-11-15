import type { AffiliateApplicationListItem } from "@/lib/affiliate/application";
import { TOUCH_TARGET } from "@/components/ui/touch-target";

const STATUS_LABEL = {
  pending: "Pending",
  approved: "Approved",
  declined: "Declined",
} as const;

/** Approve or decline a pending application. Approved rows show the issued code. */
export function AffiliateReviewList({
  applications,
  pendingId,
  notice,
  onApprove,
  onDecline,
}: {
  applications: readonly AffiliateApplicationListItem[];
  pendingId: string | null;
  notice: string | null;
  onApprove: (applicationId: string) => void;
  onDecline: (applicationId: string) => void;
}) {
  if (applications.length === 0) {
    return <p className="text-sm text-zinc-400">No applications yet.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {notice ? (
        <p role="status" className="text-sm text-zinc-300">
          {notice}
        </p>
      ) : null}
      <ul className="flex flex-col gap-4">
        {applications.map((application) => {
          const busy = pendingId === application.id;
          return (
            <li key={application.id} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
              <article className="flex flex-col gap-3" aria-labelledby={`affiliate-app-${application.id}`}>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 id={`affiliate-app-${application.id}`} className="text-base font-semibold text-zinc-50">
                    {application.name}
                  </h2>
                  <p className="text-xs font-medium uppercase tracking-wide text-zinc-400">
                    {STATUS_LABEL[application.status]}
                  </p>
                </div>
                <p className="text-sm text-zinc-300">{application.email}</p>
                {application.organization ? (
                  <p className="text-sm text-zinc-300">{application.organization}</p>
                ) : null}
                {application.role ? <p className="text-sm text-zinc-400">{application.role}</p> : null}
                {application.website ? (
                  <p className="break-all text-sm text-zinc-400">{application.website}</p>
                ) : null}
                {application.message ? (
                  <p className="whitespace-pre-wrap text-sm text-zinc-300">{application.message}</p>
                ) : null}
                {application.code ? (
                  <p className="text-sm text-zinc-200">
                    Code <span className="font-mono">{application.code}</span>
                  </p>
                ) : null}
                {application.status === "pending" ? (
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      className={`rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-70 ${TOUCH_TARGET}`}
                      onClick={() => onApprove(application.id)}
                    >
                      Approve <span className="sr-only">{application.name}</span>
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      className={`rounded-md border border-zinc-700 px-4 py-2 text-sm font-medium text-zinc-200 transition-colors hover:border-zinc-500 disabled:opacity-70 ${TOUCH_TARGET}`}
                      onClick={() => onDecline(application.id)}
                    >
                      Decline <span className="sr-only">{application.name}</span>
                    </button>
                  </div>
                ) : null}
              </article>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
