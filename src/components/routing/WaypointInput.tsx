"use client";

import { useState, useRef, useEffect } from "react";
import { X, MapPin, Loader2 } from "lucide-react";
import { useGeocode } from "@/hooks/useGeocode";
import type { NormalizedGeocodingResult } from "@/lib/server/services/geocoding";

interface WaypointInputProps {
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
  onSelect: (result: NormalizedGeocodingResult) => void;
  onClear: () => void;
  icon?: React.ReactNode;
}

export function WaypointInput({
  placeholder,
  value,
  onChange,
  onSelect,
  onClear,
  icon,
}: WaypointInputProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const { results, isLoading } = useGeocode(value);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    setOpen(results.length > 0 && value.length >= 2);
  }, [results, value]);

  function handleSelect(result: NormalizedGeocodingResult) {
    onSelect(result);
    setOpen(false);
  }

  return (
    <div ref={containerRef} className="relative">
      <div className="flex items-center gap-2 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2">
        <span className="text-[hsl(var(--muted-foreground))]">
          {icon ?? <MapPin className="h-4 w-4" />}
        </span>
        <input
          type="text"
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-[hsl(var(--muted-foreground))]"
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => results.length > 0 && setOpen(true)}
        />
        {isLoading && <Loader2 className="h-4 w-4 animate-spin text-[hsl(var(--muted-foreground))]" />}
        {value && !isLoading && (
          <button
            type="button"
            onClick={onClear}
            className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {open && (
        <ul className="absolute left-0 right-0 top-full z-50 mt-1 max-h-48 overflow-y-auto rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--popover))] shadow-md">
          {results.map((result) => (
            <li key={result.id}>
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))]"
                onClick={() => handleSelect(result)}
              >
                <span className="font-medium">{result.name}</span>
                {result.displayName && result.displayName !== result.name && (
                  <span className="block truncate text-xs text-[hsl(var(--muted-foreground))]">
                    {result.displayName}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
