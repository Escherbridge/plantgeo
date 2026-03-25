"use client";

import { Layers, Wind, Leaf } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { trpc } from "@/lib/trpc/client";
import { type SoilProperty, SOIL_PROPERTY_LABELS } from "@/components/map/layers/SoilLayer";
import { useSoilStore } from "@/stores/soil-store";
import { EROSION_COLORS, type ErosionClass } from "@/lib/server/services/usle";
import { CARBON_COLORS, classifyCarbonPotential, type CarbonClass } from "@/components/map/layers/CarbonPotentialLayer";
import { LayerToggle } from "@/components/ui/layer-toggle";
import type { InterventionType } from "@/lib/server/services/carbon-potential";

interface SoilPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Point to query for soil + intervention suitability */
  queryPoint?: { lat: number; lon: number } | null;
}

const SOIL_PROPERTY_OPTIONS: { value: SoilProperty; label: string }[] = [
  { value: "phh2o", label: "pH" },
  { value: "soc", label: "Organic Carbon" },
  { value: "clay", label: "Clay Content" },
  { value: "sand", label: "Sand Content" },
  { value: "nitrogen", label: "Nitrogen" },
  { value: "bdod", label: "Bulk Density" },
  { value: "cec", label: "CEC" },
];

const EROSION_CLASSES: ErosionClass[] = ["very_low", "low", "moderate", "high", "very_high"];
const EROSION_CLASS_LABELS: Record<ErosionClass, string> = {
  very_low: "Very Low",
  low: "Low",
  moderate: "Moderate",
  high: "High",
  very_high: "Very High",
};

const CARBON_CLASSES: CarbonClass[] = ["very_low", "low", "medium", "high", "very_high"];
const CARBON_CLASS_LABELS: Record<CarbonClass, string> = {
  very_low: "Very Low (<0.2 tC/ha/yr)",
  low: "Low (0.2–0.5 tC/ha/yr)",
  medium: "Medium (0.5–1.0 tC/ha/yr)",
  high: "High (1.0–2.0 tC/ha/yr)",
  very_high: "Very High (>2.0 tC/ha/yr)",
};

const INTERVENTION_LABELS: Record<InterventionType, string> = {
  reforestation: "Reforestation",
  silvopasture: "Silvopasture",
  cover_cropping: "Cover Cropping",
  biochar: "Biochar",
  keyline: "Keyline Design",
};

function ColorLegendRow({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-4 h-3 rounded-sm shrink-0" style={{ backgroundColor: color }} />
      <span className="text-[hsl(var(--muted-foreground))] text-xs">{label}</span>
    </div>
  );
}

function SuitabilityBar({ score }: { score: number }) {
  const color =
    score >= 75
      ? "#4caf50"
      : score >= 50
      ? "#8bc34a"
      : score >= 30
      ? "#ff9800"
      : "#f44336";
  return (
    <div className="flex items-center gap-2 flex-1">
      <div className="flex-1 h-2 rounded-full bg-[hsl(var(--muted))] overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${score}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-xs font-medium w-8 text-right" style={{ color }}>
        {score}
      </span>
    </div>
  );
}

export function SoilPanel({
  open,
  onOpenChange,
  queryPoint,
}: SoilPanelProps) {
  const { property: selectedProperty, setProperty } = useSoilStore();

  const soilQuery = trpc.environmental.getSoilProperties.useQuery(
    { lat: queryPoint?.lat ?? 0, lon: queryPoint?.lon ?? 0 },
    { enabled: open && !!queryPoint }
  );

  const suitabilityQuery = trpc.environmental.getInterventionSuitability.useQuery(
    { lat: queryPoint?.lat ?? 0, lon: queryPoint?.lon ?? 0 },
    { enabled: open && !!queryPoint }
  );

  function handlePropertyChange(prop: SoilProperty) {
    setProperty(prop);
  }

  const soil = soilQuery.data;
  const suitability = suitabilityQuery.data;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" onOpenChange={onOpenChange}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Layers className="h-5 w-5 text-amber-700" />
            Soil Health & Carbon
          </SheetTitle>
        </SheetHeader>

        <LayerToggle layerId="soil" label="Soil Properties" />

        <div className="mt-4 overflow-y-auto max-h-[calc(100vh-8rem)]">
          <Tabs defaultValue="properties">
            <TabsList className="w-full">
              <TabsTrigger value="properties" className="flex-1 text-xs">
                <Layers className="h-3.5 w-3.5 mr-1" />
                Soil
              </TabsTrigger>
              <TabsTrigger value="erosion" className="flex-1 text-xs">
                <Wind className="h-3.5 w-3.5 mr-1" />
                Erosion
              </TabsTrigger>
              <TabsTrigger value="carbon" className="flex-1 text-xs">
                <Leaf className="h-3.5 w-3.5 mr-1" />
                Carbon
              </TabsTrigger>
            </TabsList>

            {/* Soil Properties Tab */}
            <TabsContent value="properties" className="flex flex-col gap-4 mt-4">
              <div className="flex flex-col gap-2">
                <p className="text-xs text-[hsl(var(--muted-foreground))]">Display property</p>
                <div className="flex flex-wrap gap-1.5">
                  {SOIL_PROPERTY_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                        selectedProperty === opt.value
                          ? "bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] border-transparent"
                          : "border-[hsl(var(--border))] text-[hsl(var(--foreground))] bg-[hsl(var(--card))]"
                      }`}
                      onClick={() => handlePropertyChange(opt.value)}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Active property info */}
              <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                <p className="text-xs font-semibold mb-1 text-[hsl(var(--foreground))]">
                  {SOIL_PROPERTY_LABELS[selectedProperty]}
                </p>
                <p className="text-[10px] text-[hsl(var(--muted-foreground))] leading-relaxed">
                  Live WMS raster from SoilGrids (ISRIC). Showing 0-5 cm depth mean values globally.
                  Colors are rendered server-side by the WMS service.
                </p>
              </div>

              {/* Queried point data */}
              {!queryPoint && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Click anywhere on the map to query soil properties at that point.
                </p>
              )}

              {queryPoint && soilQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Querying SoilGrids…
                </p>
              )}

              {soil && queryPoint && (
                <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-2">
                  <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                    Point ({queryPoint.lat.toFixed(4)}, {queryPoint.lon.toFixed(4)})
                  </p>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                    <span className="text-[hsl(var(--muted-foreground))]">pH</span>
                    <span className="font-medium">{soil.ph.toFixed(1)}</span>
                    <span className="text-[hsl(var(--muted-foreground))]">Organic Carbon</span>
                    <span className="font-medium">{soil.organicCarbon.toFixed(1)} g/kg</span>
                    <span className="text-[hsl(var(--muted-foreground))]">Nitrogen</span>
                    <span className="font-medium">{soil.nitrogen.toFixed(2)} g/kg</span>
                    <span className="text-[hsl(var(--muted-foreground))]">Bulk Density</span>
                    <span className="font-medium">{soil.bulkDensity.toFixed(2)} g/cm³</span>
                    <span className="text-[hsl(var(--muted-foreground))]">CEC</span>
                    <span className="font-medium">{soil.cec.toFixed(1)} cmol/kg</span>
                    <span className="text-[hsl(var(--muted-foreground))]">OCD</span>
                    <span className="font-medium">{soil.ocd.toFixed(1)} kg/m³</span>
                  </div>
                </div>
              )}
            </TabsContent>

            {/* Erosion Risk Tab */}
            <TabsContent value="erosion" className="flex flex-col gap-4 mt-4">
              <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                  Erosion Risk Classes
                </p>
                <div className="flex flex-col gap-1">
                  {EROSION_CLASSES.map((cls) => (
                    <ColorLegendRow
                      key={cls}
                      color={EROSION_COLORS[cls]}
                      label={EROSION_CLASS_LABELS[cls]}
                    />
                  ))}
                </div>
              </div>

              <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-2">
                <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                  USLE K-Factor Method
                </p>
                <p className="text-[10px] text-[hsl(var(--muted-foreground))] leading-relaxed">
                  Erosion risk is estimated using the Universal Soil Loss Equation (USLE).
                  K-factor is approximated from organic carbon and bulk density.
                  LS-factor is derived from slope. C-factor is assigned by land cover type.
                </p>
              </div>

              {suitability && (
                <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-1">
                  <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                    Point Erosion Risk
                  </p>
                  <div className="flex items-center gap-2 mt-1">
                    <span
                      className="w-3 h-3 rounded-full shrink-0"
                      style={{
                        backgroundColor:
                          EROSION_COLORS[suitability.erosionClass as ErosionClass] ?? "#888",
                      }}
                    />
                    <span className="text-xs font-medium capitalize">
                      {EROSION_CLASS_LABELS[suitability.erosionClass as ErosionClass] ??
                        suitability.erosionClass}{" "}
                      (score: {suitability.erosionRisk})
                    </span>
                  </div>
                </div>
              )}
            </TabsContent>

            {/* Carbon Potential Tab */}
            <TabsContent value="carbon" className="flex flex-col gap-4 mt-4">
              <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                <p className="text-xs font-semibold mb-2 text-[hsl(var(--foreground))]">
                  Sequestration Potential
                </p>
                <div className="flex flex-col gap-1">
                  {CARBON_CLASSES.map((cls) => (
                    <ColorLegendRow
                      key={cls}
                      color={CARBON_COLORS[cls]}
                      label={CARBON_CLASS_LABELS[cls]}
                    />
                  ))}
                </div>
              </div>

              {!queryPoint && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Click on the map to see intervention-specific carbon potential.
                </p>
              )}

              {queryPoint && suitabilityQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Calculating intervention suitability…
                </p>
              )}

              {suitability && (
                <>
                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-2">
                    <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                      Intervention Suitability &amp; Carbon Potential
                    </p>
                    {(Object.entries(suitability.interventions) as [InterventionType, (typeof suitability.interventions)[InterventionType]][]).map(
                      ([type, data]) => {
                        const carbonClass = classifyCarbonPotential(
                          data.carbonPotential.potentialGain
                        );
                        return (
                          <div
                            key={type}
                            className="border-t border-[hsl(var(--border))] pt-2 first:border-0 first:pt-0"
                          >
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-xs font-medium text-[hsl(var(--foreground))]">
                                {INTERVENTION_LABELS[type]}
                              </span>
                              <span
                                className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                                style={{
                                  backgroundColor: CARBON_COLORS[carbonClass] + "33",
                                  color: CARBON_COLORS[carbonClass],
                                }}
                              >
                                {data.carbonPotential.potentialGain.toFixed(2)} tC/ha/yr
                              </span>
                            </div>
                            <SuitabilityBar score={data.suitabilityScore} />
                            <p className="text-[10px] text-[hsl(var(--muted-foreground))] mt-1">
                              {data.rationale}
                            </p>
                            <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                              Confidence:{" "}
                              <span className="capitalize font-medium">
                                {data.carbonPotential.confidenceClass}
                              </span>{" "}
                              · ~{data.carbonPotential.yearsToSaturation} yrs to saturation
                            </p>
                          </div>
                        );
                      }
                    )}
                  </div>
                </>
              )}
            </TabsContent>
          </Tabs>
        </div>
      </SheetContent>
    </Sheet>
  );
}
