import type { PlaceDetail } from "./types";

interface PlaceDetailsSectionProps {
  place: PlaceDetail;
}

/** Panel item 1: place details. Renders only source-backed, nonpersonal fields. */
export function PlaceDetailsSection({ place }: PlaceDetailsSectionProps) {
  return (
    <section aria-labelledby="land-context-place-details-heading">
      <h3 id="land-context-place-details-heading" className="text-sm font-semibold text-zinc-100">
        Place details
      </h3>

      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-zinc-300">
        <dt className="text-zinc-500">Selection</dt>
        <dd>{place.selectionLabel}</dd>

        {place.parcelOrTractId ? (
          <>
            <dt className="text-zinc-500">Parcel/tract ID</dt>
            <dd>{place.parcelOrTractId}</dd>
          </>
        ) : null}

        {place.county || place.state ? (
          <>
            <dt className="text-zinc-500">County/state</dt>
            <dd>{[place.county, place.state].filter(Boolean).join(", ")}</dd>
          </>
        ) : null}

        {place.acreage ? (
          <>
            <dt className="text-zinc-500">Acreage</dt>
            <dd>
              {place.acreage.value} {place.acreage.unit}
              <span className="text-zinc-500"> -- {place.acreage.sourceLabel}</span>
              {place.acreage.method ? <span className="text-zinc-500"> ({place.acreage.method})</span> : null}
            </dd>
          </>
        ) : null}

        {place.ownershipCategory ? (
          <>
            <dt className="text-zinc-500">Ownership category</dt>
            <dd>{place.ownershipCategory}</dd>
          </>
        ) : null}

        {place.managementCategory ? (
          <>
            <dt className="text-zinc-500">Management</dt>
            <dd>{place.managementCategory}</dd>
          </>
        ) : null}
      </dl>

      {place.useFacets && place.useFacets.length > 0 ? (
        <ul className="mt-2 space-y-1 text-xs text-zinc-300">
          {place.useFacets.map((facet) => (
            <li key={facet.label}>
              {facet.label}: {facet.value}{" "}
              <span className="text-zinc-500">
                ({facet.sourceUrl ? (
                  <a href={facet.sourceUrl} target="_blank" rel="noreferrer" className="underline">
                    {facet.sourceLabel}
                  </a>
                ) : (
                  facet.sourceLabel
                )})
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {place.officialRecordUrl ? (
        <a
          href={place.officialRecordUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-block text-xs text-sky-400 underline"
        >
          {place.officialRecordLabel ?? "Official source record"}
        </a>
      ) : null}
    </section>
  );
}
