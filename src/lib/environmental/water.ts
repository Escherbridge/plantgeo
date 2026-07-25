export interface WaterGauge {
  siteNo: string;
  siteName: string;
  lat: number;
  lon: number;
  flowCfs: number | null;
  percentile: number | null;
  condition:
    | "above_normal"
    | "normal"
    | "below_normal"
    | "low"
    | "critically_low"
    | "unknown";
  trend: "rising" | "stable" | "declining" | null;
  updatedAt: string;
}

export interface GroundwaterWell {
  siteNo: string;
  siteName: string;
  lat: number;
  lon: number;
  depthFt: number | null;
  trend: "rising" | "stable" | "declining" | "critical";
  updatedAt: string;
}
