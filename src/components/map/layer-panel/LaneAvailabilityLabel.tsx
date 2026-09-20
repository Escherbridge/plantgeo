import type { SliderLayerCapability } from "@/types/time-slider";

function cadence(days: number | null | undefined): string {
  if (days == null) return "Source cadence unknown";
  return days === 1 ? "Daily source" : `Source every ${days} days`;
}

function refresh(seconds: number | null | undefined): string {
  if (seconds == null) return "Refresh schedule not reported.";
  if (seconds % 86400 === 0) {
    const days = seconds / 86400;
    return days === 1 ? "Scheduled refresh daily." : `Scheduled refresh every ${days} days.`;
  }
  if (seconds === 3600) return "Scheduled refresh hourly.";
  if (seconds % 3600 === 0) return `Scheduled refresh every ${seconds / 3600} hours.`;
  if (seconds === 60) return "Scheduled refresh every minute.";
  if (seconds % 60 === 0) return `Scheduled refresh every ${seconds / 60} minutes.`;
  return `Scheduled refresh every ${seconds} seconds.`;
}

/** Source timing disclosure; see ../AGENTS.md, Lane availability labels. */
export function LaneAvailabilityLabel({ capability, label }: {
  capability: SliderLayerCapability;
  label: string;
}) {
  const timing = capability.freshness;
  return (
    <details className="text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]">
      <summary
        className="cursor-pointer rounded-(--radius) focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[hsl(var(--ring))]"
      >
        <span className="sr-only">{label} availability and refresh details: </span>
        {capability.latestObservedDate === null
          ? "Latest available date unknown"
          : <>Latest available day <time dateTime={capability.latestObservedDate}>{capability.latestObservedDate}</time></>}
        <span className="block">
          {cadence(timing?.sourceCadenceDays)}
          {timing?.publicationLagDays != null && ` · ${timing.publicationLagDays}-day configured delay`}
          {" · "}{refresh(timing?.refreshIntervalSeconds)}
        </span>
      </summary>
      <p className="mt-1">
        {timing?.publicationLagDays == null
          ? "Publication delay not reported."
          : `Configured publication delay: ${timing.publicationLagDays} ${timing.publicationLagDays === 1 ? "day" : "days"}.`}
        {" "}{refresh(timing?.refreshIntervalSeconds)}
        {timing?.expectedHorizonDay && <> Target date from configured delay: <time dateTime={timing.expectedHorizonDay}>{timing.expectedHorizonDay}</time>.</>}
        {" "}A scheduled refresh is a check for new data, not a guarantee of publication.
        {" "}Availability describes this lane; coverage can vary by date and location.
      </p>
    </details>
  );
}
