"use client";

import { useState } from "react";

type MapStyle = "dark" | "light" | "satellite";

export default function EmbedCodeGenerator() {
  const [lat, setLat] = useState("37.7749");
  const [lng, setLng] = useState("-122.4194");
  const [zoom, setZoom] = useState("10");
  const [style, setStyle] = useState<MapStyle>("dark");
  const [markers, setMarkers] = useState("");
  const [width, setWidth] = useState("800");
  const [height, setHeight] = useState("500");
  const [copied, setCopied] = useState(false);

  const baseUrl =
    typeof window !== "undefined"
      ? `${window.location.protocol}//${window.location.host}`
      : "";

  const params = new URLSearchParams();
  params.set("center", `${lat},${lng}`);
  params.set("zoom", zoom);
  params.set("style", style);
  if (markers.trim()) params.set("markers", markers.trim());

  const embedSrc = `${baseUrl}/embed?${params.toString()}`;
  const iframeCode = `<iframe\n  src="${embedSrc}"\n  width="${width}"\n  height="${height}"\n  frameborder="0"\n  allowfullscreen\n  style="border:0"\n></iframe>`;

  function handleCopy() {
    navigator.clipboard.writeText(iframeCode).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div className="space-y-1">
          <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
            Latitude
          </label>
          <input
            type="number"
            value={lat}
            onChange={(e) => setLat(e.target.value)}
            className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
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
            className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
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
            className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
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
            className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
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
            className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
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
            className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
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
          className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
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
            className="rounded-md bg-[hsl(var(--primary))] px-3 py-1.5 text-xs font-medium text-[hsl(var(--primary-foreground))] hover:opacity-90 transition-opacity"
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>
        <pre className="overflow-x-auto rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted))] p-4 text-xs text-[hsl(var(--muted-foreground))]">
          {iframeCode}
        </pre>
      </div>

      <div className="space-y-2">
        <p className="text-sm font-medium text-[hsl(var(--foreground))]">Preview</p>
        <div
          className="overflow-hidden rounded-md border border-[hsl(var(--border))]"
          style={{ height: Math.min(Number(height) || 500, 400) }}
        >
          <iframe
            src={embedSrc}
            width="100%"
            height="100%"
            frameBorder="0"
            allowFullScreen
            style={{ border: 0 }}
          />
        </div>
      </div>
    </div>
  );
}
