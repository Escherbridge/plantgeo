export interface ActionNetworkPointProperties {
  voteCount: number;
  featureCount: number;
}

export type ActionNetworkPoint = GeoJSON.Feature<
  GeoJSON.Point,
  ActionNetworkPointProperties
>;

interface ClusterAccumulator {
  featureCount: number;
  centroidWeight: number;
  latWeight: number;
  lonWeight: number;
  voteCount: number;
}

export interface ActionNetworkClusterFilters {
  minimumVotes?: number;
  minimumFeatureCount?: number;
}

function getCellSizeDegrees(zoom: number): number {
  const normalizedZoom = Math.min(Math.max(Math.floor(zoom), 0), 16);
  return 3.2 / 2 ** normalizedZoom;
}

/** Clusters a bounded point set without converting centroid weights into votes. */
export function clusterActionNetworkPoints(
  features: ActionNetworkPoint[],
  zoom: number,
  filters: ActionNetworkClusterFilters
): GeoJSON.FeatureCollection<GeoJSON.Point, ActionNetworkPointProperties> {
  const cellSize = getCellSizeDegrees(zoom);
  const clusters = new Map<string, ClusterAccumulator>();
  const minimumVotes = filters.minimumVotes ?? 0;
  const minimumFeatureCount = filters.minimumFeatureCount ?? 1;

  for (const feature of features) {
    const { voteCount, featureCount } = feature.properties;
    if (voteCount < minimumVotes || featureCount < minimumFeatureCount) continue;

    const [longitude, latitude] = feature.geometry.coordinates;
    const centroidWeight = Math.max(voteCount, featureCount, 1);
    const key = `${Math.floor(longitude / cellSize)}:${Math.floor(latitude / cellSize)}`;
    const current = clusters.get(key) ?? {
      featureCount: 0,
      centroidWeight: 0,
      latWeight: 0,
      lonWeight: 0,
      voteCount: 0,
    };

    current.featureCount += featureCount;
    current.centroidWeight += centroidWeight;
    current.lonWeight += longitude * centroidWeight;
    current.latWeight += latitude * centroidWeight;
    current.voteCount += voteCount;
    clusters.set(key, current);
  }

  return {
    type: "FeatureCollection",
    features: [...clusters.entries()].map(([id, cluster]) => ({
      type: "Feature",
      id,
      geometry: {
        type: "Point",
        coordinates: [
          cluster.lonWeight / cluster.centroidWeight,
          cluster.latWeight / cluster.centroidWeight,
        ],
      },
      properties: {
        voteCount: cluster.voteCount,
        featureCount: cluster.featureCount,
      },
    })),
  };
}
