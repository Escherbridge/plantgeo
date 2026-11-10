import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono, Newsreader } from "next/font/google";
import { Providers } from "@/lib/providers";
import { ApplicationShell } from "@/components/layout/ApplicationShell";
import { Toaster } from "@/components/ui/toast";
import { SiteDiscoveryJsonLd, siteDiscoveryMetadata } from "@/lib/site-discovery";
import { SITE_ORIGIN } from "@/lib/public-site";
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

const OG_IMAGE_URL = `${SITE_ORIGIN}/og.png`;

export const metadata: Metadata = {
  ...siteDiscoveryMetadata,
  title: "PlantGeo - 3D Geospatial Platform",
  description:
    "Open-source 3D mapping platform for wildfire prevention, ecosystem monitoring, and geospatial intelligence",
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "32x32 48x48" },
      { url: "/icon-32.png", sizes: "32x32", type: "image/png" },
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  openGraph: {
    ...siteDiscoveryMetadata.openGraph,
    images: [
      {
        url: OG_IMAGE_URL,
        width: 1200,
        height: 630,
        alt: "PlantGeo",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    images: [OG_IMAGE_URL],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning className={editorialFontVariables}>
      <body className="antialiased">
        <SiteDiscoveryJsonLd />
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
