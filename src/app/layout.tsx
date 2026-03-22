import type { Metadata } from "next";
import { Providers } from "@/lib/providers";
import { Toaster } from "@/components/ui/toast";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "PlantGeo - 3D Geospatial Platform",
  description:
    "Open-source 3D mapping platform for wildfire prevention, ecosystem monitoring, and geospatial intelligence",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="antialiased">
        <Providers>{children}</Providers>
        <Toaster />
        <script
          dangerouslySetInnerHTML={{
            __html: `if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/sw.js'); }`,
          }}
        />
      </body>
    </html>
  );
}
