import { Metadata } from "next";
import { TrackingAdminDashboard } from "@/components/admin/tracking/TrackingAdminDashboard";

export const metadata: Metadata = {
  title: "Asset Tracking - Platform Admin | PlantGeo",
  description: "Fleet assets, geofences and alerts over the tracking.* schema.",
};

/** Role gating for this page lives in the segment layout (src/app/admin/layout.tsx). */
export default function AdminTrackingPage() {
  return (
    <main className="min-h-screen bg-slate-950 pt-20 pb-16">
      <TrackingAdminDashboard />
    </main>
  );
}
