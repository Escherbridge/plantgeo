"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { GeoJSONSource } from "maplibre-gl";
import { useMap } from "@/lib/map/map-context";
import { boundaryPreview, finishBoundary, MAX_BOUNDARY_VERTICES, type BoundaryGeometry, type BoundaryMode, type BoundaryPoint } from "@/lib/map/intervention-boundary";

const SOURCE = "intervention-boundary-draft";
const LAYERS = [SOURCE + "-fill", SOURCE + "-line", SOURCE + "-points"];
const buttonClass = "min-h-11 rounded border px-3 text-sm disabled:opacity-40";

/** Owns one temporary map interaction and returns a validated site to its form. */
export function InterventionBoundaryEditor({ mode, initial, onSave, onCancel }: {
  mode: BoundaryMode;
  initial?: BoundaryGeometry | null;
  onSave: (geometry: BoundaryGeometry) => void;
  onCancel: () => void;
}) {
  const map = useMap();
  const [points, setPoints] = useState<BoundaryPoint[]>(() => {
    if (initial?.type === "Point" && mode === "point") return [initial.coordinates as BoundaryPoint];
    if (initial?.type === "Polygon" && mode === "polygon") return initial.coordinates[0].slice(0, -1) as BoundaryPoint[];
    return [];
  });
  const [next, setNext] = useState<BoundaryPoint | null>(null);
  const [error, setError] = useState<string | null>(null);
  const surface = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const snapshot = useRef({ mode, points, next });
  snapshot.current = { mode, points, next };
  const actions = useRef({ finish: () => {}, cancel: onCancel });

  function finish() {
    try { onSave(finishBoundary(mode, points)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "The boundary could not be saved."); }
  }
  actions.current = { finish, cancel: onCancel };

  function add(point: BoundaryPoint) {
    setError(null);
    setPoints((current) => {
      if (mode === "point") return [point];
      if (mode === "rectangle" && current.length === 2) return [point];
      if (current.length >= MAX_BOUNDARY_VERTICES) {
        setError(`Use at most ${MAX_BOUNDARY_VERTICES} vertices. Undo or finish the boundary.`);
        return current;
      }
      return [...current, point];
    });
  }

  useEffect(() => {
    if (!map) return;
    const previousFocus = document.activeElement;
    const dock = document.getElementById("map-manager-dock");
    const previousVisibility = dock?.style.visibility ?? "";
    if (dock) dock.style.visibility = "hidden";
    const canvas = map.getCanvas();
    const previousCursor = canvas.style.cursor;
    const handlers = [map.dragPan, map.dragRotate, map.scrollZoom, map.boxZoom, map.doubleClickZoom, map.touchZoomRotate, map.touchPitch, map.keyboard];
    const enabled = handlers.map((handler) => handler.isEnabled());
    handlers.forEach((handler) => handler.disable());
    canvas.style.cursor = "crosshair";
    surface.current?.focus();
    const center = map.getCenter();
    setNext([center.lng, center.lat]);

    const paint = () => {
      const state = snapshot.current;
      const data = boundaryPreview(state.mode, state.points, state.next);
      if (!map.getSource(SOURCE)) map.addSource(SOURCE, { type: "geojson", data });
      else (map.getSource(SOURCE) as GeoJSONSource).setData(data);
      if (!map.getLayer(LAYERS[0])) map.addLayer({ id: LAYERS[0], type: "fill", source: SOURCE, filter: ["==", "$type", "Polygon"], paint: { "fill-color": "#f59e0b", "fill-opacity": 0.2 } });
      if (!map.getLayer(LAYERS[1])) map.addLayer({ id: LAYERS[1], type: "line", source: SOURCE, filter: ["==", "$type", "LineString"], paint: { "line-color": "#f59e0b", "line-width": 3 } });
      if (!map.getLayer(LAYERS[2])) map.addLayer({ id: LAYERS[2], type: "circle", source: SOURCE, filter: ["==", "$type", "Point"], paint: { "circle-radius": ["case", ["==", ["get", "first"], true], 8, 5], "circle-color": ["case", ["==", ["get", "next"], true], "#ffffff", "#f59e0b"], "circle-stroke-width": 2, "circle-stroke-color": "#111827" } });
    };
    map.on("style.load", paint);
    try { paint(); }
    catch (cause) {
      if (!(cause instanceof Error) || cause.message !== "Style is not done loading.") setError("The boundary preview is unavailable. Cancel drawing and reload the map.");
    }
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Tab") {
        const focusable = Array.from(dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), [tabindex='0']") ?? []);
        const current = focusable.indexOf(document.activeElement as HTMLElement);
        if ((event.shiftKey && current <= 0) || (!event.shiftKey && current === focusable.length - 1)) {
          event.preventDefault(); focusable[event.shiftKey ? focusable.length - 1 : 0]?.focus();
        }
        return;
      }
      if (["r", "t", "g", "1", "2", "3"].includes(event.key.toLowerCase()) || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k")) {
        event.preventDefault(); event.stopImmediatePropagation(); return;
      }
      if (event.key === "Escape") {
        event.preventDefault(); event.stopImmediatePropagation(); actions.current.cancel(); return;
      }
      if (event.ctrlKey && event.key === "Enter") {
        event.preventDefault(); event.stopImmediatePropagation(); actions.current.finish(); return;
      }
      if (event.target !== surface.current) return;
      event.stopPropagation();
      if (event.key === "Enter" && snapshot.current.next) {
        event.preventDefault();
        const point = snapshot.current.next;
        setPoints((current) => snapshot.current.mode === "point" ? [point] : snapshot.current.mode === "rectangle" && current.length === 2 ? [point] : current.length < MAX_BOUNDARY_VERTICES ? [...current, point] : current);
      } else if (event.key === "Backspace" || event.key === "Delete") {
        event.preventDefault(); setPoints((current) => current.slice(0, -1));
      } else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        const point = map.project(snapshot.current.next ?? [center.lng, center.lat]);
        const step = event.shiftKey ? 30 : 5;
        const candidate = map.unproject([Math.max(0, Math.min(canvas.clientWidth, point.x + (event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0))), Math.max(0, Math.min(canvas.clientHeight, point.y + (event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0)))]);
        setNext([candidate.lng, candidate.lat]);
      }
    };
    window.addEventListener("keydown", keydown, true);
    return () => {
      window.removeEventListener("keydown", keydown, true);
      map.off("style.load", paint);
      LAYERS.toReversed().forEach((id) => { if (map.getLayer(id)) map.removeLayer(id); });
      if (map.getSource(SOURCE)) map.removeSource(SOURCE);
      handlers.forEach((handler, i) => { if (enabled[i]) handler.enable(); });
      canvas.style.cursor = previousCursor;
      if (dock) dock.style.visibility = previousVisibility;
      if (previousFocus instanceof HTMLElement) previousFocus.focus();
    };
  }, [map]);

  useEffect(() => {
    const source = map?.getSource(SOURCE) as GeoJSONSource | undefined;
    source?.setData(boundaryPreview(mode, points, next));
  }, [map, mode, points, next]);

  if (!map) return <div role="alert">The map is not ready. <button type="button" onClick={onCancel}>Return to recommendation</button></div>;

  function coordinate(clientX: number, clientY: number): BoundaryPoint | null {
    if (!map) return null;
    const bounds = map.getCanvas().getBoundingClientRect();
    if (clientX < bounds.left || clientX > bounds.right || clientY < bounds.top || clientY > bounds.bottom) return null;
    const point = map.unproject([clientX - bounds.left, clientY - bounds.top]);
    return [point.lng, point.lat];
  }

  return createPortal(<div ref={dialog} role="dialog" aria-modal="true" aria-label="Draw intervention site" className="fixed inset-0 z-[60]">
    <div ref={surface} tabIndex={0} role="application" aria-label="Intervention boundary map" aria-describedby="boundary-instructions"
      className="absolute inset-0 cursor-crosshair touch-none outline-none focus-visible:ring-4 focus-visible:ring-amber-500"
      onPointerMove={(event) => setNext(coordinate(event.clientX, event.clientY))}
      onPointerUp={(event) => { if (!event.isPrimary || event.button !== 0) return; event.preventDefault(); const point = coordinate(event.clientX, event.clientY); if (point) { add(point); surface.current?.focus(); } }} />
    <section aria-label="Boundary drawing controls" className="absolute bottom-3 left-1/2 w-[min(94vw,38rem)] -translate-x-1/2 rounded-xl border bg-[hsl(var(--background))] p-4 shadow-xl text-[hsl(var(--foreground))]">
      <h2 className="font-semibold">{mode === "rectangle" ? "Draw a rectangle" : mode === "point" ? "Choose a point" : "Draw a site boundary"}</h2>
      <p id="boundary-instructions" className="text-sm">{mode === "rectangle" ? "Tap two opposite corners." : mode === "point" ? "Tap the map to place a point; tap again to move it." : "Tap each vertex. The larger amber dot is the first vertex."} White dot previews the next point. Arrow keys move it, Enter adds it, Backspace undoes, Ctrl+Enter finishes, Escape cancels.</p>
      <p role="status" className="my-2 text-sm">{points.length} {mode === "rectangle" ? "corners" : "vertices"} selected{next ? ` · Next: ${next[1].toFixed(5)}, ${next[0].toFixed(5)}` : ""}</p>
      {error && <p role="alert" className="mb-2 text-sm text-red-600">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <button className={buttonClass} type="button" onClick={() => setPoints((current) => current.slice(0, -1))} disabled={!points.length}>Undo vertex</button>
        <button className={buttonClass} type="button" onClick={() => { setPoints([]); setError(null); }} disabled={!points.length}>Clear / restart</button>
        <button className={buttonClass} type="button" onClick={() => surface.current?.focus()}>Focus map</button>
        <button className={buttonClass} type="button" onClick={onCancel}>Cancel drawing</button>
        <button className={buttonClass + " bg-amber-500 text-black"} type="button" onClick={finish}>Finish boundary</button>
      </div>
    </section>
  </div>, document.body);
}
