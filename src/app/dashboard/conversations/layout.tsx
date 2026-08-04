/** Scroll shell for the AI conversation list and transcripts, which sit below the global top bar. */
export default function ConversationsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="viewport-below-top-bar flex flex-col">
      {/* See src/app/dashboard/conversations/AGENTS.md for why this owns the scroll surface. */}
      <main className="min-h-0 w-full flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}
