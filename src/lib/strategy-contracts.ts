export interface StrategyScore {
  strategyId: string;
  name: string;
  score: number;
  factors: {
    waterStress: number;
    soilHealth: number;
    fireRisk: number;
    vegetationDegradation: number;
    communityDemand: number;
  };
  confidence: "high" | "medium" | "low";
  topReasons: string[];
}

export interface Supplier {
  id: string;
  name: string;
  strategyTypes: string[];
  region: string;
  rating: number;
  productsAvailable: string[];
  url?: string;
}
