"use client";

import { Layers, Wind, Leaf } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { trpc } from "@/lib/trpc/client";
import { type SoilProperty, SOIL_PROPERTY_LABELS } from "@/components/map/layers/SoilLayer";
import { useSoilStore } from "@/stores/soil-store";
import { EROSION_COLORS, type ErosionClass } from "@/lib/environmental/erosion";
import { CARBON_COLORS, classifyCarbonPotential, type CarbonClass } from "@/components/map/layers/CarbonPotentialLayer";
import { LayerToggle } from "@/components/ui/layer-toggle";
import type { InterventionType } from "@/lib/environmental/intervention";
import { ENVIRONMENTAL_TILES_CONFIGURED } from "@/lib/vegetation";
import { useLayerVisibility } from "@/lib/map/layer-toggle-context";
import { useSoilSurveyQuery } from "@/hooks/useViewportProxiedLayers";

interface SoilPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Point to query for soil + intervention suitability */
  queryPoint?: { lat: number; lon: number } | null;
  /** The map's viewport, handed down by PanelManager exactly as the other panels get it. */
  bbox?: string;
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
  bbox,
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

  // The SSURGO collection the map is drawing, read back so this panel can say when what
  // is drawn is partial or missing. The same hook LayerManager calls, on the bbox
  // PanelManager derived once, so the two observers share one react-query entry: opening
  // this panel never issues an upstream request the map was not already making.
  // `useLayerVisibility` and not `useLayerToggle`: the panel must not become the sole
  // requester of a layer governance withholds from the map.
  const soilSurveyVisible = useLayerVisibility()["soil-survey"];
  const soilSurveyQuery = useSoilSurveyQuery(bbox, {
    enabled: open && soilSurveyVisible,
  });
  const soilSurvey = soilSurveyQuery.data;
  // USDA holds more map units than it serves for one view and returned a subset; the
  // count below then describes part of the view, not the view.
  const soilSurveyTruncated = soilSurvey?.truncated === true;
  // A provider fault, not a coverage answer: `features: []` here means SDA did not
  // answer, which the map paints exactly like ground the survey found nothing on.
  const soilSurveyUnavailable = soilSurvey?.availability === "unavailable";
  const soilSurveyCount = soilSurvey?.features.length ?? 0;
  // Map units SDA did serve whose geometry this reader could not parse. They are dropped
  // rather than drawn at a guessed outline, so the ground under them paints blank -- a
  // gap in what we can read, which must never be captioned as unsurveyed ground.
  const soilSurveyUnreadable = soilSurvey?.unreadableGeometries ?? 0;

  const soil = soilQuery.data;
  const suitability = suitabilityQuery.data;
  const suitabilityAvailable = suitability?.availability === "published";

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
        {/* The registry routes "soil-survey" to this panel, and the Legend only lists
            warehouse-backed layers (soil-survey's warehouseLayerName is null) -- so without
            a switch here the SSURGO polygons SoilSurveyLayer renders could never be turned
            on. Same pattern as the "watersheds" toggle in WaterPanel.tsx. */}
        <LayerToggle layerId="soil-survey" label="Soil Survey (SSURGO)" />

        {/* What the survey layer is actually drawing. Both dishonest cases -- a subset
            served past USDA's row ceiling, and an upstream fault -- reach the map as
            polygons that stop, indistinguishable from ground the survey found nothing on,
            so the panel is the only surface that can tell them apart. Same treatment
            WaterPanel gives the watershed collection. The registry's unavailableReason
            channel cannot carry this: soil-survey is upstream-proxied with no
            warehouseLayerName, so it has no per-day capability to be unavailable at, and
            a reason there would also disable the switch while the layer is still drawing. */}
        {soilSurveyVisible && (
          <div className="mt-1.5 flex flex-col gap-1.5">
            {soilSurveyQuery.isLoading && (
              <p
                role="status"
                aria-live="polite"
                className="text-xs text-[hsl(var(--muted-foreground))]"
              >
                Loading the USDA soil survey for this view…
              </p>
            )}

            {soilSurveyUnavailable && (
              <p
                role="status"
                aria-live="polite"
                className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
              >
                USDA Soil Data Access did not return a map-unit table for this view.
                Nothing is drawn — that is a provider fault, not an absence of soil. The
                response was not cached, so returning to this view asks USDA again.
              </p>
            )}

            {soilSurveyTruncated && (
              <p
                role="status"
                aria-live="polite"
                className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
              >
                USDA surveyed more map units than this view draws. The first{" "}
                {soilSurveyCount} are shown — what is drawn is a subset, so ground left
                blank here is not necessarily unsurveyed. Zoom in for complete coverage.
              </p>
            )}

            {/* Rows SDA served that never became polygons. Same honest-gap principle as
                `truncated`: the map paints the ground under them exactly like ground the
                survey found nothing on, and only this note can tell the two apart. */}
            {soilSurveyUnreadable > 0 && (
              <p
                role="status"
                aria-live="polite"
                className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
              >
                USDA returned {soilSurveyUnreadable} map unit
                {soilSurveyUnreadable === 1 ? "" : "s"} whose boundary could not be read,
                so {soilSurveyUnreadable === 1 ? "it is" : "they are"} not drawn. Blank
                ground here is a gap in what this reader could parse, not an absence of
                soil.
              </p>
            )}

            {/* The viewport area ceiling lives on the server, so the client learns it has
                been exceeded only from the rejected request; a transient SDA outage
                arrives the same way, hence one note covering both. */}
            {soilSurveyQuery.isError && (
              <p
                role="alert"
                className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]"
              >
                The soil survey could not be loaded for this view. It is served only over
                a bounded viewport area — zoom in, or try again shortly.
              </p>
            )}

            {/* Only claimed once a response actually arrived, and only for a published
                one: an undefined result is "not answered yet", not "USDA says there is
                nothing here". */}
            {soilSurvey && !soilSurveyUnavailable && !soilSurveyTruncated && (
              <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                {soilSurveyCount > 0
                  ? `${soilSurveyCount} SSURGO map units drawn for this view.`
                  : soilSurveyUnreadable > 0
                    ? "No map unit USDA returned for this view could be read, so nothing is drawn."
                    : "USDA reports no surveyed SSURGO map units in this view."}
              </p>
            )}
          </div>
        )}

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
                  {ENVIRONMENTAL_TILES_CONFIGURED
                    ? "Versioned, first-party 0-5 cm mean tiles are configured."
                    : "Soil tiles are paused until a versioned warehouse release is published."}
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

              {suitabilityAvailable && suitability.erosionClass && suitability.erosionRisk !== null && (
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

              {suitability?.availability === "unavailable" && (
                <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                  Intervention effects are unavailable until a validated,
                  evidence-linked model release is published.
                </p>
              )}

              {queryPoint && soilQuery.isError && (
                <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]">
                  Point soil properties are unavailable until a validated
                  warehouse release is published.
                </p>
              )}

              {suitabilityAvailable && (
                <>
                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-2">
                    <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                      Intervention Suitability &amp; Carbon Potential
                    </p>
                    {(Object.entries(suitability.interventions) as [InterventionType, NonNullable<(typeof suitability.interventions)[InterventionType]>][]).map(
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
