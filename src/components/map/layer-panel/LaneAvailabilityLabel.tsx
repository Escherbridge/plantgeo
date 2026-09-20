import type { SliderLayerCapability } from "@/types/time-slider";

function cadence(days: number | null | undefined): string | null {
  if (days == null) return null;
  return days === 1 ? "Source daily" : `Source every ${days} days`;
}

function refresh(seconds: number | null | undefined): string | null {
  if (seconds == null) return null;
  if (seconds % 86400 === 0) {
    const days = seconds / 86400;
    return days === 1 ? "Checked daily" : `Checked every ${days} days`;
  }
  if (seconds === 3600) return "Checked hourly";
  if (seconds % 3600 === 0) return `Checked every ${seconds / 3600} hours`;
  if (seconds === 60) return "Checked every minute";
  if (seconds % 60 === 0) return `Checked every ${seconds / 60} minutes`;
  return `Checked every ${seconds} seconds`;
}

/** Source timing disclosure; see ../AGENTS.md, Lane availability labels. */
export function LaneAvailabilityLabel({ capability, label }: {
  capability: SliderLayerCapability;
  label: string;
}) {
  const timing = capability.freshness;
  const sourceCadence = cadence(timing?.sourceCadenceDays);
  const refreshSchedule = refresh(timing?.refreshIntervalSeconds);
  return (
    <p className="text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]">
      <span className="sr-only">{label} timing: </span>
      {capability.latestObservedDate === null ? (
        "Latest date unknown"
      ) : (
        <>
          Available through{" "}
          <time dateTime={capability.latestObservedDate}>{capability.latestObservedDate}</time>
        </>
      )}
      {sourceCadence && <> · {sourceCadence}</>}
      {refreshSchedule && <> · {refreshSchedule}</>}
    </p>
  );
}
