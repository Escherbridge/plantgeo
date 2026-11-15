"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";
import { MapWebGLFallback } from "@/components/map/MapWebGLFallback";
import { markWebGLUnavailable } from "@/components/map/webgl-status";

interface MapErrorBoundaryProps {
  children: ReactNode;
  onError?: () => void;
}

interface MapErrorBoundaryState {
  failed: boolean;
}

/**
 * Catches a render failure inside the map tree so `/` and `/plan/*` keep the
 * rest of the route. WebGL init is also guarded in `MapView`; this is the net
 * for a throw that still escapes into render.
 */
export class MapErrorBoundary extends Component<MapErrorBoundaryProps, MapErrorBoundaryState> {
  state: MapErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): MapErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Map failed to render", error, info.componentStack);
    markWebGLUnavailable();
    this.props.onError?.();
  }

  render(): ReactNode {
    if (this.state.failed) return <MapWebGLFallback />;
    return this.props.children;
  }
}
