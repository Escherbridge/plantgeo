import type { Metadata } from "next";
import { ConsoleShell } from "@/components/onboarding/ConsoleShell";
import { PRIVATE_ROUTE_ROBOTS } from "@/lib/public-site";

export const metadata: Metadata = {
  title: "PlantGeo - Organization Invitation",
  description: "Review and accept an organization invitation on PlantGeo.",
  robots: PRIVATE_ROUTE_ROBOTS,
};

/** Public shell for invitation landing pages; must render for logged-out visitors. */
export default function InviteLayout({ children }: { children: React.ReactNode }) {
  return (
    <ConsoleShell
      eyebrow="Geospatial Access"
      maxWidthClassName="max-w-md"
      cornerLabel={
        <>
          Invitation
          <br />
          Grid Ref PNW-01
        </>
      }
    >
      {children}
    </ConsoleShell>
  );
}
