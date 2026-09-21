import {
  DATA_INTERVENTION_LANES,
  DATA_PROVENANCE_LABELS,
  isDataInterventionType,
  readDataInterventionDetails,
} from "@/lib/environmental/data-intervention";

interface DataInterventionSummaryProps {
  type: unknown;
  dataDetails?: unknown;
  dataOrigin?: unknown;
}

/** Data origin stays independent of moderation status; see ../AGENTS.md. */
export function DataInterventionSummary({
  type,
  dataDetails,
  dataOrigin,
}: DataInterventionSummaryProps) {
  if (!isDataInterventionType(type)) return null;
  const details = readDataInterventionDetails(dataDetails);
  const origin = dataOrigin === "community"
    ? type === "data_collection"
      ? "Community collection plan"
      : DATA_PROVENANCE_LABELS.community
    : "Data origin unknown";
  const lane = DATA_INTERVENTION_LANES.find((entry) => entry.id === details?.lane);

  return (
    <section
      aria-label="Data provenance and collection details"
      className="my-2 space-y-2 rounded border border-current/20 p-2 text-xs"
    >
      <p className="font-semibold">{origin}</p>
      {dataOrigin === "community" && <p>Review and publication do not make this verified source data.</p>}
      {details ? (
        <dl className="space-y-1">
          <div>
            <dt className="font-medium">Data lane</dt>
            <dd>{lane?.label ?? "Unknown lane"}</dd>
          </div>
          <div>
            <dt className="font-medium">Collection method</dt>
            <dd className="whitespace-pre-wrap break-words">{details.collectionMethod}</dd>
          </div>
          {details.observedOn && (
            <div>
              <dt className="font-medium">Observation day</dt>
              <dd><time dateTime={details.observedOn}>{details.observedOn}</time></dd>
            </div>
          )}
          {details.dataUrl && (
            <div>
              <dt className="font-medium">Dataset or evidence</dt>
              <dd>
                <a href={details.dataUrl} target="_blank" rel="noopener noreferrer" className="break-all underline">
                  Open dataset or evidence
                </a>
              </dd>
            </div>
          )}
        </dl>
      ) : <p>Collection details are not available.</p>}
    </section>
  );
}
