import type { Metadata } from "next";
import { ConsoleShell } from "@/components/onboarding/ConsoleShell";

export const metadata: Metadata = {
  title: "PlantGeo - Set Up Your Organization",
  description: "Create or join an organization to start collaborating on PlantGeo.",
};

/** Shared shell for onboarding: same "geospatial access" identity as the auth flow, wider canvas. */
export default function OnboardingLayout({ children }: { children: React.ReactNode }) {
  return (
    <ConsoleShell
      eyebrow="Geospatial Access"
      cornerLabel={
        <>
          New Organization
          <br />
          Grid Ref PNW-01
        </>
      }
    >
      {children}
    </ConsoleShell>
  );
}
