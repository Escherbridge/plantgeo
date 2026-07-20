/// <reference lib="webworker" />

import {
  clusterActionNetworkPoints,
  type ActionNetworkPoint,
} from "@/lib/action-network-clustering";

const MAX_FEATURES = 2_000;
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const WORKER_PROTOCOL_VERSION = 1;

interface ActionNetworkFilters {
  minimumVotes?: number;
  minimumFeatureCount?: number;
}

interface LoadRequest {
  version: typeof WORKER_PROTOCOL_VERSION;
  id: number;
  type: "load";
  bbox: string;
  zoom: number;
  filters?: ActionNetworkFilters;
}

interface CancelRequest {
  version: typeof WORKER_PROTOCOL_VERSION;
  id: number;
  type: "cancel";
}

type WorkerRequest = LoadRequest | CancelRequest;

type ActionFeature = ActionNetworkPoint;

interface ActiveRequest {
  controller: AbortController;
  id: number;
}

const workerScope = self as DedicatedWorkerGlobalScope;
let activeRequest: ActiveRequest | null = null;

function isFiniteInteger(value: unknown, minimum: number, maximum: number) {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= minimum &&
    value <= maximum
  );
}

function validateLoadRequest(value: unknown): value is LoadRequest {
  if (!value || typeof value !== "object") return false;
  const request = value as Partial<LoadRequest>;
  if (
    request.type !== "load" ||
    request.version !== WORKER_PROTOCOL_VERSION ||
    !isFiniteInteger(request.id, 1, Number.MAX_SAFE_INTEGER) ||
    typeof request.bbox !== "string" ||
    request.bbox.length < 3 ||
    request.bbox.length > 100 ||
    typeof request.zoom !== "number" ||
    !Number.isFinite(request.zoom) ||
    request.zoom < 0 ||
    request.zoom > 22
  ) {
    return false;
  }

  const filters = request.filters;
  if (filters === undefined) return true;
  if (!filters || typeof filters !== "object") return false;
  return (
    (filters.minimumVotes === undefined ||
      isFiniteInteger(filters.minimumVotes, 0, 1_000_000)) &&
    (filters.minimumFeatureCount === undefined ||
      isFiniteInteger(filters.minimumFeatureCount, 1, 1_000_000))
  );
}

function validateFeatureCollection(value: unknown): ActionFeature[] | null {
  if (!value || typeof value !== "object") return null;
  const collection = value as {
    type?: unknown;
    features?: unknown;
  };
  if (
    collection.type !== "FeatureCollection" ||
    !Array.isArray(collection.features) ||
    collection.features.length > MAX_FEATURES
  ) {
    return null;
  }

  const features: ActionFeature[] = [];
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

  return features;
}

function buildEndpoint(request: LoadRequest): string {
  const endpoint = new URL("/api/v1/action-network", workerScope.location.origin);
  endpoint.searchParams.set("bbox", request.bbox);
  endpoint.searchParams.set("zoom", String(request.zoom));
  endpoint.searchParams.set("limit", String(MAX_FEATURES));
  if (request.filters?.minimumVotes !== undefined) {
    endpoint.searchParams.set("min_votes", String(request.filters.minimumVotes));
  }
  if (request.filters?.minimumFeatureCount !== undefined) {
    endpoint.searchParams.set(
      "min_features",
      String(request.filters.minimumFeatureCount)
    );
  }
  return endpoint.toString();
}

function postError(
  id: number,
  code: string,
  message: string,
  retryable: boolean
) {
  workerScope.postMessage({
    version: WORKER_PROTOCOL_VERSION,
    type: "error",
    id,
    error: { code, message, retryable },
  });
}

async function failedResponseError(response: Response): Promise<ActionNetworkError> {
  const fallback: ActionNetworkError = {
    code: response.status >= 500 ? "SERVER_UNAVAILABLE" : "REQUEST_REJECTED",
    message: "Action-network data could not be loaded",
    retryable: response.status >= 500 || response.status === 429,
  };
  try {
    const body = JSON.parse(await readBoundedResponseText(response)) as {
      error?: { code?: unknown; message?: unknown; retryable?: unknown };
    };
    if (body.error?.code === "ACTION_NETWORK_INACTIVE") {
      return {
        code: "ACTION_NETWORK_INACTIVE",
        message:
          typeof body.error.message === "string"
            ? body.error.message
            : "Action-network waypoints are inactive",
        retryable: false,
      };
    }
  } catch {
    // The status-derived fallback avoids trusting an invalid error payload.
  }
  return fallback;
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

async function loadActionNetwork(request: LoadRequest) {
  activeRequest?.controller.abort();
  const controller = new AbortController();
  activeRequest = { controller, id: request.id };

  try {
    const response = await fetch(buildEndpoint(request), {
      headers: { Accept: "application/geo+json, application/json" },
      signal: controller.signal,
      credentials: "same-origin",
    });
    if (activeRequest?.id !== request.id) return;
    if (!response.ok) {
      const error = await failedResponseError(response);
      postError(request.id, error.code, error.message, error.retryable);
      return;
    }

    const contentLength = Number(response.headers.get("content-length"));
    if (Number.isFinite(contentLength) && contentLength > MAX_RESPONSE_BYTES) {
      postError(
        request.id,
        "RESPONSE_TOO_LARGE",
        "Action-network response exceeded the safe size limit",
        false
      );
      return;
    }

    const text = await readBoundedResponseText(response);
    if (activeRequest?.id !== request.id) return;
    if (text.length > MAX_RESPONSE_BYTES) {
      postError(
        request.id,
        "RESPONSE_TOO_LARGE",
        "Action-network response exceeded the safe size limit",
        false
      );
      return;
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      postError(
        request.id,
        "INVALID_RESPONSE",
        "Action-network response was not valid JSON",
        true
      );
      return;
    }

    const features = validateFeatureCollection(parsed);
    if (!features) {
      postError(
        request.id,
        "INVALID_RESPONSE",
        "Action-network response did not match the data contract",
        true
      );
      return;
    }

    const data = clusterActionNetworkPoints(
      features,
      request.zoom,
      request.filters ?? {}
    );
    if (activeRequest?.id !== request.id) return;
    workerScope.postMessage({
      version: WORKER_PROTOCOL_VERSION,
      type: "success",
      id: request.id,
      data,
      metadata: {
        featureCount: data.features.length,
        observedAt: response.headers.get("x-data-observed-at"),
        revision:
          response.headers.get("x-data-revision") ?? response.headers.get("etag"),
        sourceFreshness: response.headers.get("x-data-freshness"),
      },
    });
  } catch (error) {
    if (controller.signal.aborted || activeRequest?.id !== request.id) return;
    if (error instanceof Error && error.message === "RESPONSE_TOO_LARGE") {
      postError(
        request.id,
        "RESPONSE_TOO_LARGE",
        "Action-network response exceeded the safe size limit",
        false
      );
      return;
    }
    postError(
      request.id,
      "NETWORK_ERROR",
      error instanceof TypeError
        ? "Action-network data could not be reached"
        : "Action-network processing failed",
      true
    );
  } finally {
    if (activeRequest?.id === request.id) activeRequest = null;
  }
}

workerScope.onmessage = (event: MessageEvent<WorkerRequest>) => {
  const request = event.data;
  if (
    request?.type === "cancel" &&
    request.version === WORKER_PROTOCOL_VERSION
  ) {
    if (activeRequest?.id === request.id) activeRequest.controller.abort();
    return;
  }

  if (!validateLoadRequest(request)) {
    const id =
      request && typeof request === "object" && "id" in request
        ? Number(request.id)
        : 0;
    postError(id, "INVALID_REQUEST", "Worker request was invalid", false);
    return;
  }

  void loadActionNetwork(request);
};
