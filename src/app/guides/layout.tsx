/** Hourly ISR so a guide edit is not pinned by the static one-year CDN cache. */
export const revalidate = 3600;

export default function GuidesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
