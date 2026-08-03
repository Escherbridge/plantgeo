type FormNoticeTone = "success" | "error" | "info";

const TONE_STYLES: Record<FormNoticeTone, string> = {
  success: "border-emerald-800/60 bg-emerald-950/40 text-emerald-300",
  error: "border-red-900/60 bg-red-950/40 text-red-300",
  info: "border-zinc-700 bg-zinc-800/60 text-zinc-300",
};

/** Small inline banner for auth-flow success/error/info states. */
export function FormNotice({
  tone = "info",
  children,
}: {
  tone?: FormNoticeTone;
  children: React.ReactNode;
}) {
  return (
    <p
      role={tone === "error" ? "alert" : "status"}
      className={`rounded-md border px-3 py-2 text-xs leading-relaxed ${TONE_STYLES[tone]}`}
    >
      {children}
    </p>
  );
}
