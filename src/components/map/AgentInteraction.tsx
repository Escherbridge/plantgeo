"use client";

import { useState } from "react";
// In the full project, this uses trpc react hooks:
// import { trpc } from "@/lib/trpc/client";
// Since this is a scaffold, we will mock the type invocation.

export function AgentInteraction({ coordinates, onClose }: { coordinates: [number, number] | null; onClose: () => void }) {
  // Mock trpc query
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<any>(null);

  // In production, this would be:
  // const { data, isLoading } = trpc.agent.invokeRecommendationAgent.useQuery(
  //   { lat: coordinates[1], lon: coordinates[0] },
  //   { enabled: !!coordinates }
  // );

  // Mocking the data flow for the prototype
  if (coordinates && !data && loading) {
    setTimeout(() => {
      setData({
        primarySolution: "Agroforestry",
        rationale: "Based on the localized historical water table and current soil context, Agroforestry provides optimal canopy shading.",
        recommendedPlants: [{ scientific_name: "Quercus suber" }],
        recommendedTooling: [{ name: "Drip Irrigation System" }]
      });
      setLoading(false);
    }, 1500);
  }

  if (!coordinates) return null;

  return (
    <div className="fixed bottom-4 right-4 w-96 bg-slate-900 border border-slate-700 text-white rounded-lg shadow-xl p-4 z-50">
      <div className="flex justify-between items-center mb-4">
        <h3 className="font-bold text-lg text-emerald-400">Strategic Intelligence Agent</h3>
        <button onClick={onClose} className="text-slate-400 hover:text-white">✕</button>
      </div>

      <div className="text-sm text-slate-300 mb-4">
        Coordinates: {coordinates[1].toFixed(4)}, {coordinates[0].toFixed(4)}
      </div>

      {loading ? (
        <div className="flex justify-center p-8">
          <div className="animate-spin text-emerald-500 text-2xl">⏳</div>
        </div>
      ) : data ? (
        <div className="space-y-4">
          <div className="bg-slate-800 p-3 rounded">
            <h4 className="font-semibold text-white mb-1">Recommended Approach</h4>
            <p className="text-emerald-300 text-lg">{data.primarySolution}</p>
          </div>
          
          <div className="bg-slate-800 p-3 rounded">
            <h4 className="font-semibold text-white mb-1">Agent Rationale</h4>
            <p className="text-sm text-slate-300">{data.rationale}</p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="bg-slate-800 p-3 rounded">
              <h4 className="font-semibold text-white mb-1">Plants</h4>
              <ul className="text-sm text-slate-300 list-disc ml-4">
                {data.recommendedPlants.map((p: any, i: number) => (
                  <li key={i}>{p.scientific_name}</li>
                ))}
              </ul>
            </div>
            <div className="bg-slate-800 p-3 rounded">
              <h4 className="font-semibold text-white mb-1">Tooling</h4>
              <ul className="text-sm text-slate-300 list-disc ml-4">
                {data.recommendedTooling.map((t: any, i: number) => (
                  <li key={i}>{t.name}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
