import { sanitizeOfficeContact } from "./land-context-utils";
import type { AdviserCardData } from "./types";

interface AdviserCardProps {
  card: AdviserCardData;
}

/**
 * Related-adviser card. Deliberately styled differently from `OfficeCard` (dashed border,
 * violet accent, "Adviser" eyebrow label) so a reader cannot mistake a topic-matched adviser
 * for a responsible authority -- spec panel item 6 requires this be visually distinct, not just
 * a text label difference.
 */
export function AdviserCard({ card }: AdviserCardProps) {
  const adviser = sanitizeOfficeContact(card.adviser);

  return (
    <div className="rounded-lg border border-dashed border-violet-700 bg-violet-950/20 p-3">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-violet-400">Adviser</div>
      <div className="mt-1 font-medium text-zinc-100">{adviser.name}</div>
      {adviser.role ? <div className="text-xs text-zinc-400">{adviser.role}</div> : null}

      <div className="mt-2 text-xs text-zinc-300">
        <div>Topic: {card.topic}</div>
        <div>Service area: {card.serviceArea}</div>
        {card.programLimits ? <div className="text-zinc-500">Program limits: {card.programLimits}</div> : null}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-300">
        {adviser.publicPhone ? (
          <a href={`tel:${adviser.publicPhone}`} className="underline hover:text-zinc-100">
            {adviser.publicPhone}
          </a>
        ) : null}
        {adviser.publicEmail ? (
          <a href={`mailto:${adviser.publicEmail}`} className="underline hover:text-zinc-100">
            {adviser.publicEmail}
          </a>
        ) : null}
        {adviser.officialUrl ? (
          <a href={adviser.officialUrl} target="_blank" rel="noreferrer" className="underline hover:text-zinc-100">
            Official page
          </a>
        ) : null}
      </div>

      <div className="mt-2 text-[11px] text-violet-300/80">
        Advisory fit only -- expertise does not imply approval authority.
      </div>
    </div>
  );
}
