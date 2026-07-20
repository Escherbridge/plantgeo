"use client";

import { useState, useRef, useEffect, useId } from "react";
import { X, MapPin, Loader2 } from "lucide-react";
import { useGeocode } from "@/hooks/useGeocode";
import type { NormalizedGeocodingResult } from "@/lib/geocoding";

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
  const [activeIndex, setActiveIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const listboxId = useId();
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
    setActiveIndex(0);
  }, [results, value]);

  function handleSelect(result: NormalizedGeocodingResult) {
    onSelect(result);
    setOpen(false);
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" && results.length > 0) {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => Math.min(index + 1, results.length - 1));
    } else if (event.key === "ArrowUp" && results.length > 0) {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter" && open && results[activeIndex]) {
      event.preventDefault();
      handleSelect(results[activeIndex]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <div className="flex items-center gap-2 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2">
        <span aria-hidden="true" className="text-[hsl(var(--muted-foreground))]">
          {icon ?? <MapPin className="h-4 w-4" />}
        </span>
        <input
          type="text"
          role="combobox"
          aria-label={placeholder}
          aria-autocomplete="list"
          aria-controls={listboxId}
          aria-expanded={open}
          aria-activedescendant={open ? `${listboxId}-option-${activeIndex}` : undefined}
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-[hsl(var(--muted-foreground))]"
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => results.length > 0 && setOpen(true)}
          onKeyDown={handleKeyDown}
        />
        {isLoading && <Loader2 className="h-4 w-4 animate-spin text-[hsl(var(--muted-foreground))]" />}
        {value && !isLoading && (
          <button
            type="button"
            onClick={onClear}
            aria-label={`Clear ${placeholder}`}
            className="inline-flex min-h-11 min-w-11 items-center justify-center rounded text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {open && (
        <ul
          id={listboxId}
          role="listbox"
          className="absolute left-0 right-0 top-full z-50 mt-1 max-h-48 overflow-y-auto rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--popover))] shadow-md"
        >
          {results.map((result, index) => (
            <li
              key={result.id}
              id={`${listboxId}-option-${index}`}
              role="option"
              aria-selected={index === activeIndex}
              className={`cursor-pointer px-3 py-2 text-left text-sm hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))] ${
                index === activeIndex
                  ? "bg-[hsl(var(--accent))] text-[hsl(var(--accent-foreground))]"
                  : ""
              }`}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => handleSelect(result)}
            >
                <span className="font-medium">{result.name}</span>
                {result.displayName && result.displayName !== result.name && (
                  <span className="block truncate text-xs text-[hsl(var(--muted-foreground))]">
                    {result.displayName}
                  </span>
                )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
