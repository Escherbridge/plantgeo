import type { Metadata } from "next";
import { ConsoleShell } from "@/components/onboarding/ConsoleShell";

export const metadata: Metadata = {
  title: "PlantGeo - Join an Organization",
  description: "Redeem a join code to join an organization on PlantGeo.",
};

/** Public shell for join-code landing pages; must render for logged-out visitors. */
export default function JoinLayout({ children }: { children: React.ReactNode }) {
  return (
    <ConsoleShell
      eyebrow="Geospatial Access"
      maxWidthClassName="max-w-md"
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
