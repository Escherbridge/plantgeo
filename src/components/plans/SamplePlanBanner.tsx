import { SAMPLE_PLAN_LABEL } from "@/lib/plans/sample";

const LEGACY_SAMPLE_LABEL = "Sample plan — not a real customer";

/** Persistent banner distinguishing the live demo parcel from user-created site plans. */
export function SamplePlanBanner({ label = SAMPLE_PLAN_LABEL }: { label?: string | null }) {
  const displayLabel =
    !label || label === LEGACY_SAMPLE_LABEL ? SAMPLE_PLAN_LABEL : label;
  return (
    <div role="status" className="shrink-0 bg-ink px-page-inset py-snug text-paper">
      <p className="font-editorial-label text-label uppercase">{displayLabel}</p>
    </div>
  );
}
