import { useSyncExternalStore } from "react";

/**
 * Home keeps its crawlable overlay until a map actually comes up. The map and
 * that overlay are siblings, so a failed init publishes here instead of
 * throwing into the route error boundary.
 */
let unavailable = false;
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

export function markWebGLUnavailable(): void {
  if (unavailable) return;
  unavailable = true;
  emit();
}

/** A later map that does come up clears a failure left by a previous page. */
export function markWebGLAvailable(): void {
  if (!unavailable) return;
  unavailable = false;
  emit();
}

export function useWebGLUnavailable(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => unavailable,
    () => false,
  );
}

/**
 * `maplibre-gl`'s `supported()` is false when the canvas cannot create a
 * WebGL context. Test doubles that omit it are treated as able to construct
 * their own map stub.
 */
export function webGLIsAvailable(
  supported: ((options?: { failIfMajorPerformanceCaveat?: boolean }) => boolean) | undefined,
): boolean {
  if (typeof supported !== "function") return true;
  try {
    return supported({ failIfMajorPerformanceCaveat: false }) !== false;
  } catch {
    return false;
  }
}

export function isWebGLFailure(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /webgl/i.test(message);
}

/**
 * MapLibre 5 throws `Failed to initialize WebGL` from the constructor and does
 * not export `supported()`. A 2D context with no WebGL context is a real
 * browser that cannot draw the map. An environment that cannot create either
 * context (jsdom) is left unknown so test doubles can still build a map.
 */
export function browserWebGLState(): "available" | "unavailable" | "unknown" {
  if (typeof document === "undefined") return "unknown";
  // jsdom implements neither context and warns on every getContext call. That is
  // not a browser with WebGL turned off.
  if (typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent)) return "unknown";
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
    if (gl) return "available";
    if (!canvas.getContext("2d")) return "unknown";
    return "unavailable";
  } catch {
    return "unavailable";
  }
}
