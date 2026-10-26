"use client";

import { useState } from "react";
import { NO_IOS_FOCUS_ZOOM, TOUCH_TARGET } from "@/components/ui/touch-target";
import { useMapStore } from "@/stores/map-store";
import type { MapStyle } from "@/types/map";

/**
 * Builds the `/embed` iframe URL and HTML snippet from editable fields.
 *
 * Pure, and kept separate from the component, so a test can assert the exact query string
 * `EmbedMap.tsx` parses (`center`, `zoom`, `style`, `markers`) without rendering React. The
 * three params this writes are the only ones `EmbedMap.tsx` reads -- see that file's
 * `parseCenter`/`parseMarkers` and the `styleParam` lookup; a layers param was never wired
 * because `/embed` renders the basemap and markers only, never a data layer (no tRPC provider
 * in `src/app/embed/layout.tsx`).
 */
export function buildEmbedSnippet(params: {
  baseUrl: string;
  lat: string;
  lng: string;
  zoom: string;
  style: MapStyle;
  markers: string;
  width: string;
  height: string;
}): { embedSrc: string; iframeCode: string } {
  const query = new URLSearchParams();
  query.set("center", `${params.lat},${params.lng}`);
  query.set("zoom", params.zoom);
  query.set("style", params.style);
  if (params.markers.trim()) query.set("markers", params.markers.trim());

  const embedSrc = `${params.baseUrl}/embed?${query.toString()}`;
  const iframeCode = `<iframe\n  src="${embedSrc}"\n  width="${params.width}"\n  height="${params.height}"\n  frameborder="0"\n  allowfullscreen\n  style="border:0"\n></iframe>`;
  return { embedSrc, iframeCode };
}

export default function EmbedCodeGenerator() {
  // Seeded from the live camera and basemap, not a hardcoded default: sharing a link shares
  // THIS view. Read once into local state rather than live-bound, so editing a field (e.g. to
  // widen the zoom before copying) cannot be overwritten by the next pan tick.
  const initialViewport = useMapStore((state) => state.viewport);
  const initialStyle = useMapStore((state) => state.currentStyle);

  const [lat, setLat] = useState(() => initialViewport.latitude.toFixed(4));
  const [lng, setLng] = useState(() => initialViewport.longitude.toFixed(4));
  const [zoom, setZoom] = useState(() => initialViewport.zoom.toFixed(1));
  const [style, setStyle] = useState<MapStyle>(initialStyle);
  const [markers, setMarkers] = useState("");
  const [width, setWidth] = useState("800");
  const [showPreview, setShowPreview] = useState(false);
  const [height, setHeight] = useState("500");
  const [copied, setCopied] = useState(false);

  const baseUrl =
    typeof window !== "undefined"
      ? `${window.location.protocol}//${window.location.host}`
      : "";

  const { embedSrc, iframeCode } = buildEmbedSnippet({
    baseUrl,
    lat,
    lng,
    zoom,
    style,
    markers,
    width,
    height,
  });

  function handleCopy() {
    navigator.clipboard.writeText(iframeCode).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
            Latitude
          </label>
          <input
            type="number"
            value={lat}
            onChange={(e) => setLat(e.target.value)}
            className={`w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] ${TOUCH_TARGET} ${NO_IOS_FOCUS_ZOOM}`}
            placeholder="37.7749"
          />
        </div>
        <div className="space-y-1">
          <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
            Longitude
          </label>
          <input
            type="number"
            value={lng}
            onChange={(e) => setLng(e.target.value)}
            className={`w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] ${TOUCH_TARGET} ${NO_IOS_FOCUS_ZOOM}`}
            placeholder="-122.4194"
          />
        </div>
        <div className="space-y-1">
          <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
            Zoom
          </label>
          <input
            type="number"
            min="0"
            max="22"
            value={zoom}
            onChange={(e) => setZoom(e.target.value)}
            className={`w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] ${TOUCH_TARGET} ${NO_IOS_FOCUS_ZOOM}`}
            placeholder="10"
          />
        </div>
        <div className="space-y-1">
          <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
            Style
          </label>
          <select
            value={style}
            onChange={(e) => setStyle(e.target.value as MapStyle)}
            className={`w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] ${TOUCH_TARGET} ${NO_IOS_FOCUS_ZOOM}`}
          >
            <option value="dark">Dark</option>
            <option value="light">Light</option>
            <option value="satellite">Satellite</option>
          </select>
        </div>
        <div className="space-y-1">
          <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
            Width (px)
          </label>
          <input
            type="number"
            value={width}
            onChange={(e) => setWidth(e.target.value)}
            className={`w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] ${TOUCH_TARGET} ${NO_IOS_FOCUS_ZOOM}`}
            placeholder="800"
          />
        </div>
        <div className="space-y-1">
          <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
            Height (px)
          </label>
          <input
            type="number"
            value={height}
            onChange={(e) => setHeight(e.target.value)}
            className={`w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] ${TOUCH_TARGET} ${NO_IOS_FOCUS_ZOOM}`}
            placeholder="500"
          />
        </div>
      </div>

      <div className="space-y-1">
        <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
          Markers{" "}
          <span className="font-normal text-[hsl(var(--muted-foreground))]">
            (lat,lng,label|lat2,lng2,label2 — optional)
          </span>
        </label>
        <input
          type="text"
          value={markers}
          onChange={(e) => setMarkers(e.target.value)}
          className={`w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] ${TOUCH_TARGET} ${NO_IOS_FOCUS_ZOOM}`}
          placeholder="37.7749,-122.4194,San Francisco"
        />
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
            Embed Code
          </label>
          <button
            onClick={handleCopy}
            className={`rounded-md bg-[hsl(var(--primary))] px-3 py-1.5 text-xs font-medium text-[hsl(var(--primary-foreground))] hover:opacity-90 transition-opacity ${TOUCH_TARGET}`}
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>
        <pre className="overflow-x-auto rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted))] p-4 text-xs text-[hsl(var(--muted-foreground))]">
          {iframeCode}
        </pre>
      </div>

      <div className="space-y-2">
        {/* Opt-in: the preview is a second WebGL map running inside the dock. */}
        <button
          type="button"
          onClick={() => setShowPreview((shown) => !shown)}
          aria-expanded={showPreview}
          className={`text-sm font-medium text-[hsl(var(--foreground))] underline-offset-2 hover:underline ${TOUCH_TARGET}`}
        >
          {showPreview ? "Hide preview" : "Show preview"}
        </button>
        {showPreview && (
          <div
            className="overflow-hidden rounded-md border border-[hsl(var(--border))]"
            style={{ height: Math.min(Number(height) || 500, 400) }}
          >
            <iframe src={embedSrc} width="100%" height="100%" allowFullScreen style={{ border: 0 }} />
          </div>
        )}
      </div>
    </div>
  );
}
