import type { Metadata } from "next";
import { PRIVATE_ROUTE_ROBOTS } from "@/lib/public-site";
import "@/styles/globals.css";

export const metadata: Metadata = {
  robots: PRIVATE_ROUTE_ROBOTS,
};

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
