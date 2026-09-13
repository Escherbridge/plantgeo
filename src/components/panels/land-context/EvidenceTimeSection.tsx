import { StalenessIndicator } from "./StalenessIndicator";
import type { EvidenceTimeEntry } from "./types";

interface EvidenceTimeSectionProps {
  evidence: EvidenceTimeEntry[];
}

/** Panel item 5: evidence and time. Every stale/unverified/broken item gets icon + text (X4). */
export function EvidenceTimeSection({ evidence }: EvidenceTimeSectionProps) {
  return (
    <section aria-labelledby="land-context-evidence-heading">
      <h3 id="land-context-evidence-heading" className="text-sm font-semibold text-zinc-100">
        Evidence and time
      </h3>

      {evidence.length === 0 ? (
        <p className="mt-2 text-xs text-zinc-500">No evidence/version entries for this selection.</p>
      ) : (
        <ul className="mt-2 space-y-1 text-xs text-zinc-300">
          {evidence.map((entry) => (
            <li key={entry.label} className="flex flex-wrap items-center justify-between gap-2 rounded border border-zinc-800 p-2">
              <div>
                <div className="font-medium text-zinc-200">{entry.label}</div>
                {entry.sourceVersion ? <div className="text-zinc-500">Version: {entry.sourceVersion}</div> : null}
                {entry.coverage ? <div className="text-zinc-500">Coverage: {entry.coverage}</div> : null}
                {entry.contactVerifiedAt ? (
                  <div className="text-zinc-500">Contact verified: {entry.contactVerifiedAt}</div>
                ) : null}
              </div>
              <StalenessIndicator status={entry.status} note={entry.note} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
