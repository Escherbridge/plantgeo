const MAPILLARY_API_URL = "https://graph.mapillary.com";
const ACCESS_TOKEN = process.env.MAPILLARY_ACCESS_TOKEN || "";

export interface MapillaryImage {
  id: string;
  geometry: GeoJSON.Point;
  thumbUrl: string;
  compassAngle: number;
  sequenceId: string;
}

interface MapillaryApiImage {
  id: string;
  geometry: { type: "Point"; coordinates: [number, number] };
  thumb_1024_url?: string;
  thumb_2048_url?: string;
  compass_angle: number;
  sequence?: string;
  sequence_id?: string;
}

interface MapillaryApiResponse {
  data: MapillaryApiImage[];
}

function toMapillaryImage(raw: MapillaryApiImage, preferHigh = false): MapillaryImage {
  return {
    id: raw.id,
    geometry: { type: "Point", coordinates: raw.geometry.coordinates },
    thumbUrl: (preferHigh ? raw.thumb_2048_url : raw.thumb_1024_url) || raw.thumb_1024_url || raw.thumb_2048_url || "",
    compassAngle: raw.compass_angle ?? 0,
    sequenceId: raw.sequence_id ?? raw.sequence ?? "",
  };
}

export async function getImages(
  bbox: { west: number; south: number; east: number; north: number },
  limit = 100
): Promise<GeoJSON.FeatureCollection> {
  const params = new URLSearchParams({
    fields: "id,geometry,thumb_1024_url,compass_angle,sequence",
    bbox: `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`,
    limit: String(limit),
    access_token: ACCESS_TOKEN,
  });

  const response = await fetch(`${MAPILLARY_API_URL}/images?${params}`);

  if (!response.ok) {
    throw new Error(`Mapillary API error: ${response.statusText}`);
  }

  const data: MapillaryApiResponse = await response.json();

  return {
    type: "FeatureCollection",
    features: data.data.map((img) => ({
      type: "Feature",
      properties: {
        id: img.id,
        thumbUrl: img.thumb_1024_url || "",
        compassAngle: img.compass_angle ?? 0,
        sequenceId: img.sequence_id ?? img.sequence ?? "",
      },
      geometry: { type: "Point", coordinates: img.geometry.coordinates },
    })),
  };
}

export async function getImageById(id: string): Promise<MapillaryImage> {
  const params = new URLSearchParams({
    fields: "id,geometry,thumb_2048_url,compass_angle,sequence_id",
    access_token: ACCESS_TOKEN,
  });

  const response = await fetch(`${MAPILLARY_API_URL}/${id}?${params}`);

  if (!response.ok) {
    throw new Error(`Mapillary API error: ${response.statusText}`);
  }

  const raw: MapillaryApiImage = await response.json();
  return toMapillaryImage(raw, true);
}

export async function getSequence(sequenceId: string): Promise<MapillaryImage[]> {
  const params = new URLSearchParams({
    fields: "id,geometry,thumb_1024_url,compass_angle,sequence_id",
    sequence_id: sequenceId,
    limit: "200",
    access_token: ACCESS_TOKEN,
  });

  const response = await fetch(`${MAPILLARY_API_URL}/images?${params}`);

  if (!response.ok) {
    throw new Error(`Mapillary API error: ${response.statusText}`);
  }

  const data: MapillaryApiResponse = await response.json();
  return data.data.map((img) => toMapillaryImage(img));
}
