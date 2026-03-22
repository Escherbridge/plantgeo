"use client";

import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { trpc } from "@/lib/trpc/client";

interface LayerUploadProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onLayerCreated?: (layerId: string) => void;
}

interface GeoJSONFeature {
  type: "Feature";
  geometry: unknown;
  properties: Record<string, unknown>;
}

interface GeoJSONCollection {
  type: "FeatureCollection";
  features: GeoJSONFeature[];
}

function parseCSV(text: string): GeoJSONCollection {
  const lines = text.trim().split("\n");
  const headers = lines[0].split(",").map((h) => h.trim().toLowerCase());
  const latIdx = headers.findIndex((h) => h === "lat" || h === "latitude");
  const lonIdx = headers.findIndex((h) => h === "lon" || h === "longitude" || h === "lng");

  const featureList: GeoJSONFeature[] = [];
  for (let i = 1; i < lines.length; i++) {
    const values = lines[i].split(",");
    const props: Record<string, unknown> = {};
    headers.forEach((h, idx) => {
      props[h] = values[idx]?.trim();
    });
    if (latIdx !== -1 && lonIdx !== -1) {
      const lat = parseFloat(values[latIdx]);
      const lon = parseFloat(values[lonIdx]);
      if (!isNaN(lat) && !isNaN(lon)) {
        featureList.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: [lon, lat] },
          properties: props,
        });
      }
    }
  }
  return { type: "FeatureCollection", features: featureList };
}

function parseKML(text: string): GeoJSONCollection {
  const parser = new DOMParser();
  const doc = parser.parseFromString(text, "text/xml");
  const placemarks = Array.from(doc.querySelectorAll("Placemark"));
  const featureList: GeoJSONFeature[] = [];

  for (const placemark of placemarks) {
    const coordEl = placemark.querySelector("coordinates");
    if (!coordEl) continue;
    const raw = coordEl.textContent?.trim() ?? "";
    const parts = raw.split(/\s+/)[0].split(",");
    const lon = parseFloat(parts[0]);
    const lat = parseFloat(parts[1]);
    if (isNaN(lon) || isNaN(lat)) continue;

    const nameEl = placemark.querySelector("name");
    featureList.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [lon, lat] },
      properties: { name: nameEl?.textContent ?? "" },
    });
  }
  return { type: "FeatureCollection", features: featureList };
}

async function parseFile(file: File): Promise<GeoJSONCollection> {
  const text = await file.text();
  const ext = file.name.split(".").pop()?.toLowerCase();
  if (ext === "csv") return parseCSV(text);
  if (ext === "kml") return parseKML(text);
  return JSON.parse(text) as GeoJSONCollection;
}

export function LayerUpload({ open, onOpenChange, onLayerCreated }: LayerUploadProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState<string>("");

  const createLayerMutation = trpc.layers.create.useMutation();

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    const file = files[0];
    setStatus("Parsing…");

    const collection = await parseFile(file);
    const layerName = file.name.replace(/\.[^.]+$/, "");

    setStatus("Creating layer…");
    const layer = await createLayerMutation.mutateAsync({
      name: layerName,
      type: "geojson",
      description: `Uploaded from ${file.name}`,
    });

    if (layer) {
      onLayerCreated?.(layer.id);
    }
    setStatus(`Done — ${collection.features.length} features`);
    setTimeout(() => {
      setStatus("");
      onOpenChange(false);
    }, 1500);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent onOpenChange={onOpenChange}>
        <DialogHeader>
          <DialogTitle>Upload Layer Data</DialogTitle>
        </DialogHeader>

        <div
          onDragEnter={() => setDragging(true)}
          onDragLeave={() => setDragging(false)}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            handleFiles(e.dataTransfer.files);
          }}
          onClick={() => fileRef.current?.click()}
          className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-(--radius) border-2 border-dashed p-10 transition-colors ${
            dragging
              ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/5"
              : "border-[hsl(var(--border))] hover:border-[hsl(var(--primary))]/50"
          }`}
        >
          <Upload className="h-8 w-8 text-[hsl(var(--muted-foreground))]" />
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            Drop a file or click to browse
          </p>
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Supported: .geojson, .json, .csv, .kml
          </p>
        </div>

        <input
          ref={fileRef}
          type="file"
          accept=".geojson,.json,.csv,.kml"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />

        {status && (
          <p className="text-center text-sm text-[hsl(var(--muted-foreground))]">{status}</p>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
