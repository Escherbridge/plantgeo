import { SAMPLE_PLAN_LABEL } from "@/lib/plans/sample";

/** Persistent label. The sample is a demonstration, and the bar does not dismiss. */
export function SamplePlanBanner({ label = SAMPLE_PLAN_LABEL }: { label?: string | null }) {
  return (
    <div role="status" className="shrink-0 bg-ink px-page-inset py-snug text-paper">
      <p className="font-editorial-label text-label uppercase">{label || SAMPLE_PLAN_LABEL}</p>
    </div>
  );
}
