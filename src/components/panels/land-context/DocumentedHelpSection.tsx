import { RouteBadge } from "./RouteBadge";
import type { OfficeCardData } from "./types";

interface DocumentedHelpSectionProps {
  officeCards: OfficeCardData[];
}

/**
 * Panel item 4: documented help. Shows only the service actually described by the source for
 * each relationship, plus the parcel/tract/location references that help requires -- never an
 * inferred or generic capability.
 */
export function DocumentedHelpSection({ officeCards }: DocumentedHelpSectionProps) {
  const rows = officeCards.flatMap((card) =>
    card.relationships
      .filter((relationship) => relationship.documentedService)
      .map((relationship) => ({ officeName: card.office.name, relationship })),
  );

  return (
    <section aria-labelledby="land-context-help-heading">
      <h3 id="land-context-help-heading" className="text-sm font-semibold text-zinc-100">
        Documented help
      </h3>

      {rows.length === 0 ? (
        <p className="mt-2 text-xs text-zinc-500">No documented help is published for this selection yet.</p>
      ) : (
        <ul className="mt-2 space-y-2 text-xs text-zinc-300">
          {rows.map(({ officeName, relationship }) => (
            <li key={`${officeName}-${relationship.featureId}`} className="rounded border border-zinc-800 p-2">
              <div className="flex flex-wrap items-center gap-2">
                <RouteBadge
                  routeMeaning={relationship.routeMeaning}
                  introductionCapability={relationship.introductionCapability}
                />
                <span className="font-medium text-zinc-200">{officeName}</span>
              </div>
              <div className="mt-1">{relationship.documentedService}</div>
              {relationship.requiredReferences && relationship.requiredReferences.length > 0 ? (
                <div className="mt-1 text-zinc-500">
                  Required references: {relationship.requiredReferences.join(", ")}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
