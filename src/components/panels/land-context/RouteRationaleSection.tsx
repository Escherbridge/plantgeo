import { RouteBadge } from "./RouteBadge";
import type { OfficeCardData } from "./types";

interface RouteRationaleSectionProps {
  officeCards: OfficeCardData[];
}

/**
 * Panel item 3: why this route applies. Surfaces match method, relationship evidence and any
 * unresolved scope for every relationship across every office card -- distinct from
 * `RelevantPartiesSection`, which is about contacting the office, not justifying the match.
 */
export function RouteRationaleSection({ officeCards }: RouteRationaleSectionProps) {
  const rows = officeCards.flatMap((card) =>
    card.relationships.map((relationship) => ({
      officeId: card.office.id,
      officeName: card.office.name,
      relationship,
    })),
  );

  return (
    <section aria-labelledby="land-context-rationale-heading">
      <h3 id="land-context-rationale-heading" className="text-sm font-semibold text-zinc-100">
        Why this route applies
      </h3>

      {rows.length === 0 ? (
        <p className="mt-2 text-xs text-zinc-500">No route rationale available for this selection.</p>
      ) : (
        <ul className="mt-2 space-y-2 text-xs text-zinc-300">
          {rows.map(({ officeId, officeName, relationship }) => (
            <li
              key={`${officeId}-${relationship.featureId}-${relationship.routeMeaning}`}
              className="rounded border border-zinc-800 p-2"
            >
              <div className="flex flex-wrap items-center gap-2">
                <RouteBadge
                  routeMeaning={relationship.routeMeaning}
                  introductionCapability={relationship.introductionCapability}
                />
                <span className="font-medium text-zinc-200">{officeName}</span>
                <span className="text-zinc-500">-- {relationship.featureLabel}</span>
              </div>
              <div className="mt-1">Match method: {relationship.evidence.matchMethod}</div>
              <div>Evidence: {relationship.evidence.relationshipEvidence}</div>
              <div className="text-zinc-500">
                Source:{" "}
                {relationship.evidence.sourceUrl ? (
                  <a href={relationship.evidence.sourceUrl} target="_blank" rel="noreferrer" className="underline">
                    {relationship.evidence.sourceLabel}
                  </a>
                ) : (
                  relationship.evidence.sourceLabel
                )}
              </div>
              {relationship.evidence.unresolvedScope ? (
                <div className="mt-1 text-amber-400">Unresolved scope: {relationship.evidence.unresolvedScope}</div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
