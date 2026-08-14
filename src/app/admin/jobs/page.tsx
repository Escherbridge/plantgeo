import { Metadata } from "next";
import { JobRunnerDashboard } from "@/components/panels/JobRunnerDashboard";

export const metadata: Metadata = {
  title: "Job Runner - Platform Admin | PlantGeo",
  description: "Durable job lanes on the agri.job_* ledger: pause state, manual runs, run history.",
};

/** Role gating for this page lives in the segment layout (src/app/admin/layout.tsx). */
export default function AdminJobsPage() {
  return (
    <main className="min-h-screen bg-slate-950 pt-20 pb-16">
      <JobRunnerDashboard />
    </main>
  );
}
