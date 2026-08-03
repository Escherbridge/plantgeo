import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono, Newsreader } from "next/font/google";
import { Providers } from "@/lib/providers";
import { ApplicationShell } from "@/components/layout/ApplicationShell";
import { Toaster } from "@/components/ui/toast";
import "@/styles/globals.css";

/** Grotesque for display, serif for reading, mono for labels. */
const editorialDisplayFont = Archivo({
  subsets: ["latin"],
  display: "swap",
  variable: "--editorial-font-display",
});

const editorialTextFont = Newsreader({
  subsets: ["latin"],
  style: ["normal", "italic"],
  display: "swap",
  variable: "--editorial-font-text",
});

const editorialLabelFont = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--editorial-font-label",
});

const editorialFontVariables = [
  editorialDisplayFont.variable,
  editorialTextFont.variable,
  editorialLabelFont.variable,
].join(" ");

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
    <html lang="en" suppressHydrationWarning className={editorialFontVariables}>
      <body className="antialiased">
        <Providers>
          <ApplicationShell>{children}</ApplicationShell>
        </Providers>
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
