import "@/styles/globals.css";

export default function EmbedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="bg-black antialiased">{children}</body>
    </html>
  );
}
