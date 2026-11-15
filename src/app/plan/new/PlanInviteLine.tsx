/** The line above the new-plan form. An unknown code renders nothing. */
export function PlanInviteLine({ line }: { line: string | null }) {
  if (!line) return null;
  return (
    <p className="mb-snug max-w-measure font-editorial-text text-body text-ink">{line}</p>
  );
}
