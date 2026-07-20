"use client";

import { useEffect, useRef, useState } from "react";

const MAX_FEATURES = 2_000;
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const WORKER_PROTOCOL_VERSION = 1;

const EMPTY_FEATURE_COLLECTION: GeoJSON.FeatureCollection<GeoJSON.Point> = {
  type: "FeatureCollection",
  features: [],
};

export interface ActionNetworkFilters {
  minimumVotes?: number;
  minimumFeatureCount?: number;
}

export interface ActionNetworkMetadata {
  featureCount: number;
  observedAt: string | null;
  revision: string | null;
  sourceFreshness: string | null;
}

export interface ActionNetworkError {
  code: string;
  message: string;
  retryable: boolean;
}

interface WorkerSuccess {
  version: typeof WORKER_PROTOCOL_VERSION;
  type: "success";
  id: number;
  data: unknown;
  metadata: ActionNetworkMetadata;
}

interface WorkerFailure {
  version: typeof WORKER_PROTOCOL_VERSION;
  type: "error";
  id: number;
  error: ActionNetworkError;
}

type WorkerResponse = WorkerSuccess | WorkerFailure;
type WorkerState = "checking" | "available" | "unavailable";

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

async function readBoundedResponseText(response: Response): Promise<string> {
  const reader = response.body?.getReader();
  if (!reader) return "";

  const decoder = new TextDecoder();
  const chunks: string[] = [];
  let receivedBytes = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      receivedBytes += value.byteLength;
      if (receivedBytes > MAX_RESPONSE_BYTES) {
        await reader
          .cancel("response exceeds safe size limit")
          .catch(() => undefined);
        throw new Error("RESPONSE_TOO_LARGE");
      }
      chunks.push(decoder.decode(value, { stream: true }));
    }
    chunks.push(decoder.decode());
    return chunks.join("");
  } finally {
    reader.releaseLock();
  }
}

async function failedResponseCode(response: Response): Promise<string> {
  if (response.status !== 503) {
    return response.status >= 500 ? "SERVER_UNAVAILABLE" : "REQUEST_REJECTED";
  }
  try {
    const body = JSON.parse(await readBoundedResponseText(response)) as {
      error?: { code?: unknown };
    };
    return body.error?.code === "ACTION_NETWORK_INACTIVE"
      ? "ACTION_NETWORK_INACTIVE"
      : "SERVER_UNAVAILABLE";
  } catch {
    return "SERVER_UNAVAILABLE";
  }
}

function parseFeatureCollection(
  value: unknown
): GeoJSON.FeatureCollection<GeoJSON.Point> | null {
  if (!value || typeof value !== "object") return null;
  const collection = value as { type?: unknown; features?: unknown };
  if (
    collection.type !== "FeatureCollection" ||
    !Array.isArray(collection.features) ||
    collection.features.length > MAX_FEATURES
  ) {
    return null;
  }

  const features: GeoJSON.Feature<GeoJSON.Point>[] = [];
  for (const candidate of collection.features) {
    if (!candidate || typeof candidate !== "object") return null;
    const feature = candidate as {
      type?: unknown;
      geometry?: { type?: unknown; coordinates?: unknown } | null;
      properties?: Record<string, unknown> | null;
    };
    if (
      feature.type !== "Feature" ||
      feature.geometry?.type !== "Point" ||
      !Array.isArray(feature.geometry.coordinates) ||
      feature.geometry.coordinates.length < 2
    ) {
      return null;
    }

    const longitude = Number(feature.geometry.coordinates[0]);
    const latitude = Number(feature.geometry.coordinates[1]);
    const voteCount = Number(feature.properties?.voteCount);
    const featureCount = Number(feature.properties?.featureCount);
    if (
      !Number.isFinite(longitude) ||
      longitude < -180 ||
      longitude > 180 ||
      !Number.isFinite(latitude) ||
      latitude < -90 ||
      latitude > 90 ||
      !Number.isFinite(voteCount) ||
      voteCount < 0 ||
      !Number.isFinite(featureCount) ||
      featureCount < 1
    ) {
      return null;
    }

    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [longitude, latitude] },
      properties: { voteCount, featureCount },
    });
  }

  return { type: "FeatureCollection", features };
}

function buildEndpoint(
  bbox: string,
  zoom: number,
  filters: ActionNetworkFilters
) {
  const params = new URLSearchParams({
    bbox,
    zoom: String(zoom),
    limit: String(MAX_FEATURES),
  });
  if (filters.minimumVotes !== undefined) {
    params.set("min_votes", String(filters.minimumVotes));
  }
  if (filters.minimumFeatureCount !== undefined) {
    params.set("min_features", String(filters.minimumFeatureCount));
  }
  return `/api/v1/action-network?${params.toString()}`;
}

function normalizeViewportBbox(value: string): string {
  const coordinates = value.split(",").map(Number);
  if (
    coordinates.length !== 4 ||
    coordinates.some((coordinate) => !Number.isFinite(coordinate))
  ) {
    return value;
  }

  let [west, south, east, north] = coordinates;
  if (east <= west || north <= south) return value;
  south = Math.max(-90, south);
  north = Math.min(90, north);
  const longitudeSpan = east - west;

  if (!Number.isFinite(longitudeSpan) || longitudeSpan >= 360) {
    west = -180;
    east = 180;
  } else {
    west = ((((west + 180) % 360) + 360) % 360) - 180;
    east = west + longitudeSpan;
    if (east > 180) east -= 360;
  }

  return [west, south, east, north]
    .map((coordinate) => String(Number(coordinate.toFixed(6))))
    .join(",");
}

function validFilter(value: number | undefined, minimum: number) {
  return value !== undefined &&
    Number.isInteger(value) &&
    value >= minimum &&
    value <= 1_000_000
    ? value
    : undefined;
}

function isWorkerResponse(value: unknown): value is WorkerResponse {
  if (!value || typeof value !== "object") return false;
  const response = value as {
    version?: unknown;
    type?: unknown;
    id?: unknown;
    data?: unknown;
    metadata?: unknown;
    error?: unknown;
  };
  if (
    response.version !== WORKER_PROTOCOL_VERSION ||
    typeof response.id !== "number" ||
    !Number.isInteger(response.id)
  ) {
    return false;
  }
  if (response.type === "success") {
    if (!response.metadata || typeof response.metadata !== "object") return false;
    const metadata = response.metadata as Partial<ActionNetworkMetadata>;
    return (
      typeof metadata.featureCount === "number" &&
      Number.isInteger(metadata.featureCount) &&
      metadata.featureCount >= 0 &&
      metadata.featureCount <= MAX_FEATURES &&
      isNullableString(metadata.observedAt) &&
      isNullableString(metadata.revision) &&
      isNullableString(metadata.sourceFreshness)
    );
  }
  if (
    response.type !== "error" ||
    !response.error ||
    typeof response.error !== "object"
  ) {
    return false;
  }
  const error = response.error as Partial<ActionNetworkError>;
  return (
    typeof error.code === "string" &&
    typeof error.message === "string" &&
    typeof error.retryable === "boolean"
  );
}

/** Loads the bounded access-controlled waypoint response off the UI thread. */
export function useActionNetworkFeatures(
  bbox: string,
  zoom: number,
  enabled: boolean,
  filters?: ActionNetworkFilters
) {
  const workerRef = useRef<Worker | null>(null);
  const requestIdRef = useRef(0);
  const [workerState, setWorkerState] = useState<WorkerState>("checking");
  const [data, setData] =
    useState<GeoJSON.FeatureCollection<GeoJSON.Point>>(
      EMPTY_FEATURE_COLLECTION
    );
  const [metadata, setMetadata] = useState<ActionNetworkMetadata | null>(null);
  const [error, setError] = useState<ActionNetworkError | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [retryGeneration, setRetryGeneration] = useState(0);

  useEffect(() => {
    let mounted = true;
    const setAvailability = (state: WorkerState) => {
      queueMicrotask(() => {
        if (mounted) setWorkerState(state);
      });
    };
    if (typeof Worker === "undefined") {
      setAvailability("unavailable");
      return () => {
        mounted = false;
      };
    }

    let worker: Worker;
    try {
      worker = new Worker(
        new URL("../workers/action-network.worker.ts", import.meta.url),
        { name: "action-network", type: "module" }
      );
    } catch {
      setAvailability("unavailable");
      return () => {
        mounted = false;
      };
    }

    workerRef.current = worker;
    setAvailability("available");
    worker.onmessage = (event: MessageEvent<unknown>) => {
      if (!isWorkerResponse(event.data)) return;
      const response = event.data;
      if (response.id !== requestIdRef.current) return;

      if (response.type === "error") {
        setData(EMPTY_FEATURE_COLLECTION);
        setMetadata(null);
        setError(response.error);
        setIsLoading(false);
        return;
      }

      const nextData = parseFeatureCollection(response.data);
      if (!nextData) {
        setData(EMPTY_FEATURE_COLLECTION);
        setMetadata(null);
        setError({
          code: "INVALID_WORKER_RESPONSE",
          message: "Action-network processing returned invalid data",
          retryable: true,
        });
        setIsLoading(false);
        return;
      }

      setData(nextData);
      setMetadata(response.metadata);
      setError(null);
      setIsLoading(false);
    };
    worker.onerror = () => {
      worker.terminate();
      if (workerRef.current === worker) workerRef.current = null;
      requestIdRef.current += 1;
      setData(EMPTY_FEATURE_COLLECTION);
      setMetadata(null);
      setError({
        code: "WORKER_UNAVAILABLE",
        message: "Action-network worker stopped; action-network data is unavailable",
        retryable: true,
      });
      setIsLoading(false);
      setWorkerState("unavailable");
    };

    return () => {
      mounted = false;
      worker.terminate();
      if (workerRef.current === worker) workerRef.current = null;
    };
  }, []);

  const minimumVotes = validFilter(filters?.minimumVotes, 0);
  const minimumFeatureCount = validFilter(filters?.minimumFeatureCount, 1);
  const normalizedBbox = normalizeViewportBbox(bbox);

  useEffect(() => {
    if (!enabled) {
      const id = requestIdRef.current + 1;
      requestIdRef.current = id;
      queueMicrotask(() => {
        if (requestIdRef.current !== id) return;
        setData(EMPTY_FEATURE_COLLECTION);
        setMetadata(null);
        setError(null);
        setIsLoading(false);
      });
      return;
    }
    if (workerState === "checking") {
      const id = requestIdRef.current;
      queueMicrotask(() => {
        if (requestIdRef.current !== id) return;
        setData(EMPTY_FEATURE_COLLECTION);
        setMetadata(null);
      });
      return;
    }

    const id = requestIdRef.current + 1;
    requestIdRef.current = id;
    queueMicrotask(() => {
      if (requestIdRef.current !== id) return;
      setData(EMPTY_FEATURE_COLLECTION);
      setMetadata(null);
      setIsLoading(true);
      setError(null);
    });

    const normalizedFilters: ActionNetworkFilters = {
      minimumVotes,
      minimumFeatureCount,
    };
    const worker = workerRef.current;
    if (workerState === "available" && worker) {
      worker.postMessage({
        version: WORKER_PROTOCOL_VERSION,
        type: "load",
        id,
        bbox: normalizedBbox,
        zoom,
        filters: normalizedFilters,
      });
      return () =>
        worker.postMessage({
          version: WORKER_PROTOCOL_VERSION,
          type: "cancel",
          id,
        });
    }

    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(
          buildEndpoint(normalizedBbox, zoom, normalizedFilters),
          {
            credentials: "same-origin",
            headers: { Accept: "application/geo+json, application/json" },
            signal: controller.signal,
          }
        );
        if (!response.ok) {
          throw new Error(await failedResponseCode(response));
        }

        const contentLength = Number(response.headers.get("content-length"));
        if (
          Number.isFinite(contentLength) &&
          contentLength > MAX_RESPONSE_BYTES
        ) {
          throw new Error("RESPONSE_TOO_LARGE");
        }

        const text = await readBoundedResponseText(response);
        if (text.length > MAX_RESPONSE_BYTES) {
          throw new Error("RESPONSE_TOO_LARGE");
        }
        if (controller.signal.aborted || id !== requestIdRef.current) return;

        const nextData = parseFeatureCollection(JSON.parse(text) as unknown);
        if (!nextData) throw new Error("INVALID_RESPONSE");

        setData(nextData);
        setMetadata({
          featureCount: nextData.features.length,
          observedAt: response.headers.get("x-data-observed-at"),
          revision:
            response.headers.get("x-data-revision") ??
            response.headers.get("etag"),
          sourceFreshness: response.headers.get("x-data-freshness"),
        });
        setError(null);
        setIsLoading(false);
      } catch (cause) {
        if (controller.signal.aborted || id !== requestIdRef.current) return;
        const knownCodes = new Set([
          "SERVER_UNAVAILABLE",
          "REQUEST_REJECTED",
          "ACTION_NETWORK_INACTIVE",
          "RESPONSE_TOO_LARGE",
          "INVALID_RESPONSE",
        ]);
        const code =
          cause instanceof Error && knownCodes.has(cause.message)
            ? cause.message
            : cause instanceof SyntaxError
              ? "INVALID_RESPONSE"
              : "NETWORK_ERROR";
        setData(EMPTY_FEATURE_COLLECTION);
        setMetadata(null);
        setError({
          code,
          message: "Action-network data could not be loaded",
          retryable:
            code === "SERVER_UNAVAILABLE" || code === "NETWORK_ERROR",
        });
        setIsLoading(false);
      }
    })();

    return () => controller.abort();
  }, [
    enabled,
    minimumFeatureCount,
    minimumVotes,
    normalizedBbox,
    workerState,
    zoom,
    retryGeneration,
  ]);

  return {
    data: enabled ? data : EMPTY_FEATURE_COLLECTION,
    error,
    isLoading: enabled && isLoading,
    isWorkerAvailable: workerState === "available",
    metadata,
    retry: () => setRetryGeneration((generation) => generation + 1),
  };
}
