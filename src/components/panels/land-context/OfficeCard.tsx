import { sanitizeOfficeContact } from "./land-context-utils";
import { RouteBadge } from "./RouteBadge";
import type { OfficeCardData } from "./types";

interface OfficeCardProps {
  card: OfficeCardData;
}

/**
 * One deduplicated office, all of its relationships. Never spreads `card.office` directly --
 * always renders through `sanitizeOfficeContact`, so a private field cannot leak through here
 * even if an upstream object briefly carried one. This is a "responsible authority" card; see
 * `AdviserCard.tsx` for the visually distinct related-adviser styling the spec requires.
 */
export function OfficeCard({ card }: OfficeCardProps) {
  const office = sanitizeOfficeContact(card.office);

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-900 p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-medium text-zinc-100">{office.name}</div>
          {office.role ? <div className="text-xs text-zinc-400">{office.role}</div> : null}
        </div>
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-300">
        {office.publicPhone ? (
          <a href={`tel:${office.publicPhone}`} className="underline hover:text-zinc-100">
            {office.publicPhone}
          </a>
        ) : null}
        {office.publicEmail ? (
          <a href={`mailto:${office.publicEmail}`} className="underline hover:text-zinc-100">
            {office.publicEmail}
          </a>
        ) : null}
        {office.formUrl ? (
          <a href={office.formUrl} target="_blank" rel="noreferrer" className="underline hover:text-zinc-100">
            Contact form
          </a>
        ) : null}
        {office.officialUrl ? (
          <a href={office.officialUrl} target="_blank" rel="noreferrer" className="underline hover:text-zinc-100">
            Official page
          </a>
        ) : null}
      </div>

      <ul className="mt-3 space-y-2 border-t border-zinc-800 pt-2">
        {card.relationships.map((relationship) => (
          <li key={`${relationship.featureId}-${relationship.routeMeaning}`} className="text-xs text-zinc-300">
            <div className="flex flex-wrap items-center gap-2">
              <RouteBadge
                routeMeaning={relationship.routeMeaning}
                introductionCapability={relationship.introductionCapability}
              />
              <span className="text-zinc-400">for {relationship.featureLabel}</span>
            </div>
            <div className="mt-1 text-zinc-400">{relationship.evidence.relationshipEvidence}</div>
            {relationship.documentedService ? (
              <div className="mt-1 text-zinc-500">Documented help: {relationship.documentedService}</div>
            ) : null}
            {relationship.evidence.unresolvedScope ? (
              <div className="mt-1 text-amber-400">Unresolved scope: {relationship.evidence.unresolvedScope}</div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
