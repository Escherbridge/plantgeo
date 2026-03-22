"use client";

import React, { useCallback, useRef, useState } from "react";
import {
  FloatingToolbar,
  FloatingToolbarItem,
} from "@/components/ui/floating-toolbar";
import { useDrawingStore } from "@/stores/drawing-store";
import { useDrawing } from "@/hooks/useDrawing";
import { api } from "@/lib/trpc/client";
import type { DrawingMode } from "@/stores/drawing-store";

const TOOLS: { mode: DrawingMode; label: string; icon: React.ReactNode }[] = [
  {
    mode: "select",
    label: "Select",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path d="M5 3l14 9-7 1-4 7L5 3z" />
      </svg>
    ),
  },
  {
    mode: "point",
    label: "Point",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
      </svg>
    ),
  },
  {
    mode: "line",
    label: "Line",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path d="M4 20L20 4" />
        <circle cx="4" cy="20" r="2" fill="currentColor" />
        <circle cx="20" cy="4" r="2" fill="currentColor" />
      </svg>
    ),
  },
  {
    mode: "polygon",
    label: "Polygon",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <polygon points="12,3 21,9 18,20 6,20 3,9" />
      </svg>
    ),
  },
  {
    mode: "rectangle",
    label: "Rectangle",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <rect x="3" y="6" width="18" height="12" rx="1" />
      </svg>
    ),
  },
  {
    mode: "circle",
    label: "Circle",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 12h4" />
      </svg>
    ),
  },
  {
    mode: "freehand",
    label: "Freehand",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path d="M3 17c2-4 4-6 6-4s3 5 6 3 4-5 6-3" />
      </svg>
    ),
  },
  {
    mode: "text",
    label: "Text",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path d="M4 7V4h16v3M9 20h6M12 4v16" />
      </svg>
    ),
  },
];

interface TextDialogProps {
  onConfirm: (text: string) => void;
  onCancel: () => void;
}

function TextDialog({ onConfirm, onCancel }: TextDialogProps) {
  const [value, setValue] = useState("");
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-[var(--glass-bg)] border border-[var(--glass-border)] rounded-xl p-4 shadow-lg flex flex-col gap-3 min-w-[260px]">
        <p className="text-sm font-medium text-[hsl(var(--foreground))]">Enter label text</p>
        <input
          autoFocus
          className="rounded-lg border border-[var(--glass-border)] bg-transparent px-3 py-2 text-sm text-[hsl(var(--foreground))] outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && value.trim()) onConfirm(value.trim());
            if (e.key === "Escape") onCancel();
          }}
          placeholder="Label text..."
        />
        <div className="flex gap-2 justify-end">
          <button
            className="px-3 py-1.5 text-sm rounded-lg hover:bg-[hsl(var(--muted))] text-[hsl(var(--foreground))]"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="px-3 py-1.5 text-sm rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] disabled:opacity-50"
            disabled={!value.trim()}
            onClick={() => onConfirm(value.trim())}
          >
            Add
          </button>
        </div>
      </div>
    </div>
  );
}

interface ImportDialogProps {
  onConfirm: (geojson: string) => void;
  onCancel: () => void;
}

function ImportDialog({ onConfirm, onCancel }: ImportDialogProps) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = () => {
    try {
      JSON.parse(value);
      setError(null);
      onConfirm(value);
    } catch {
      setError("Invalid JSON");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-[var(--glass-bg)] border border-[var(--glass-border)] rounded-xl p-4 shadow-lg flex flex-col gap-3 min-w-[360px]">
        <p className="text-sm font-medium text-[hsl(var(--foreground))]">Import GeoJSON</p>
        <textarea
          autoFocus
          className="rounded-lg border border-[var(--glass-border)] bg-transparent px-3 py-2 text-sm text-[hsl(var(--foreground))] outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] h-40 resize-none font-mono"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder='Paste GeoJSON here...'
        />
        {error && <p className="text-xs text-red-500">{error}</p>}
        <div className="flex gap-2 justify-end">
          <button
            className="px-3 py-1.5 text-sm rounded-lg hover:bg-[hsl(var(--muted))] text-[hsl(var(--foreground))]"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="px-3 py-1.5 text-sm rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] disabled:opacity-50"
            disabled={!value.trim()}
            onClick={handleConfirm}
          >
            Import
          </button>
        </div>
      </div>
    </div>
  );
}

export function DrawingToolbar() {
  useDrawing();

  const drawingMode = useDrawingStore((s) => s.drawingMode);
  const setMode = useDrawingStore((s) => s.setMode);
  const features = useDrawingStore((s) => s.features);
  const addFeature = useDrawingStore((s) => s.addFeature);
  const selectedFeatureIndex = useDrawingStore((s) => s.selectedFeatureIndex);
  const removeFeature = useDrawingStore((s) => s.removeFeature);
  const clear = useDrawingStore((s) => s.clear);
  const undo = useDrawingStore((s) => s.undo);
  const undoStack = useDrawingStore((s) => s.undoStack);

  const [pendingTextCoord, setPendingTextCoord] = useState<[number, number] | null>(null);
  const [showImport, setShowImport] = useState(false);

  const createLayer = api.layers.create.useMutation();

  const handleToolClick = (mode: DrawingMode) => {
    setMode(drawingMode === mode ? null : mode);
  };

  const handleExport = useCallback(() => {
    const blob = new Blob([JSON.stringify(features, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "drawing.geojson";
    a.click();
    URL.revokeObjectURL(url);
  }, [features]);

  const handleSave = useCallback(async () => {
    await createLayer.mutateAsync({
      name: `Drawing ${new Date().toLocaleString()}`,
      type: "geojson",
      description: `${features.features.length} features`,
      style: { geojson: features },
    });
  }, [features, createLayer]);

  const handleImport = useCallback((geojsonText: string) => {
    try {
      const parsed = JSON.parse(geojsonText) as GeoJSON.FeatureCollection | GeoJSON.Feature;
      if (parsed.type === "FeatureCollection") {
        parsed.features.forEach((f) => addFeature(f));
      } else if (parsed.type === "Feature") {
        addFeature(parsed);
      }
    } catch {
      // invalid JSON already caught by dialog
    }
    setShowImport(false);
  }, [addFeature]);

  const handleDelete = useCallback(() => {
    if (selectedFeatureIndex !== null) {
      removeFeature(selectedFeatureIndex);
    } else {
      clear();
    }
  }, [selectedFeatureIndex, removeFeature, clear]);

  return (
    <>
      <FloatingToolbar className="bottom-6 top-auto left-6 translate-x-0 flex-col w-fit">
        {TOOLS.map(({ mode, label, icon }) => (
          <FloatingToolbarItem
            key={mode ?? "select"}
            icon={icon}
            label={label}
            active={drawingMode === mode}
            onClick={() => handleToolClick(mode)}
            title={label}
          />
        ))}

        <div className="w-full h-px bg-[var(--glass-border)] my-0.5" />

        <FloatingToolbarItem
          icon={
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M3 7h18M3 12h18M3 17h18" />
            </svg>
          }
          label="Import"
          onClick={() => setShowImport(true)}
          title="Import GeoJSON"
        />

        <FloatingToolbarItem
          icon={
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
            </svg>
          }
          label="Export"
          onClick={handleExport}
          disabled={features.features.length === 0}
          title="Export as GeoJSON"
        />

        <FloatingToolbarItem
          icon={
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z" />
              <path d="M17 21v-8H7v8M7 3v5h8" />
            </svg>
          }
          label="Save"
          onClick={handleSave}
          disabled={features.features.length === 0 || createLayer.isPending}
          title="Save to layer"
        />

        <FloatingToolbarItem
          icon={
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M9 14L4 9l5-5" />
              <path d="M4 9h10a5 5 0 010 10h-1" />
            </svg>
          }
          label="Undo"
          onClick={undo}
          disabled={undoStack.length === 0}
          title="Undo"
        />

        <FloatingToolbarItem
          icon={
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
            </svg>
          }
          label={selectedFeatureIndex !== null ? "Delete" : "Clear"}
          onClick={handleDelete}
          disabled={features.features.length === 0}
          title={selectedFeatureIndex !== null ? "Delete selected" : "Clear all"}
        />
      </FloatingToolbar>

      {pendingTextCoord && (
        <TextDialog
          onConfirm={(text) => {
            addFeature({
              type: "Feature",
              geometry: { type: "Point", coordinates: pendingTextCoord },
              properties: { label: text },
            });
            setPendingTextCoord(null);
            setMode(null);
          }}
          onCancel={() => {
            setPendingTextCoord(null);
            setMode(null);
          }}
        />
      )}

      {showImport && (
        <ImportDialog
          onConfirm={handleImport}
          onCancel={() => setShowImport(false)}
        />
      )}
    </>
  );
}
