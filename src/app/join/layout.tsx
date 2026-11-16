import type { Metadata } from "next";
import { ConsoleShell } from "@/components/onboarding/ConsoleShell";
import { PRIVATE_ROUTE_ROBOTS } from "@/lib/public-site";

export const metadata: Metadata = {
  title: "PlantGeo - Join an Organization",
  description: "Redeem a join code to join an organization on PlantGeo.",
  robots: PRIVATE_ROUTE_ROBOTS,
};

/** Public shell for join-code landing pages; must render for logged-out visitors. */
export default function JoinLayout({ children }: { children: React.ReactNode }) {
  return (
    <ConsoleShell
      eyebrow="Geospatial Access"
      maxWidthClassName="max-w-md"
      wayfinding
      cornerLabel={
        <>
          Join Code
          <br />
          Grid Ref PNW-01
        </>
      }
    >
      {children}
    </ConsoleShell>
  );
}
