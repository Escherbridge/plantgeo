"use client";

import { useEffect, useState } from "react";
import { Search, MapPin, Mountain, Globe, Box, Palette, Layers, RotateCcw } from "lucide-react";
import {
  CommandDialog,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
} from "@/components/ui/command";
import { useMapStore } from "@/stores/map-store";
import { useMap } from "@/lib/map/map-context";
import type { MapStyle } from "@/types/map";

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const map = useMap();
  const { toggleTerrain, toggleGlobe, toggle3D, setCurrentStyle, toggleLayer, resetView } =
    useMapStore();

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const handleSelect = (value: string) => {
    switch (value) {
      case "search-places": {
        const input = document.querySelector<HTMLInputElement>(
          "input[placeholder*='earch'], input[placeholder*='lace'], input[type='search']"
        );
        input?.focus();
        break;
      }
      case "go-to-coordinates": {
        const raw = window.prompt("Enter coordinates (lat, lon):");
        if (!raw) break;
        const parts = raw.split(",").map((s) => parseFloat(s.trim()));
        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
          map?.flyTo({ center: [parts[1], parts[0]], zoom: 14 });
        }
        break;
      }
      case "toggle-terrain":
        toggleTerrain();
        break;
      case "toggle-globe":
        toggleGlobe();
        break;
      case "toggle-3d":
        toggle3D();
        break;
      case "style-dark":
        setCurrentStyle("dark" as MapStyle);
        break;
      case "style-light":
        setCurrentStyle("light" as MapStyle);
        break;
      case "style-satellite":
        setCurrentStyle("satellite" as MapStyle);
        break;
      case "reset-view":
        resetView();
        break;
      case "toggle-fire-perimeters":
        toggleLayer("fire-perimeters");
        break;
      case "toggle-sensors":
        toggleLayer("sensors");
        break;
    }
    setOpen(false);
  };

  return (
    <CommandDialog open={open} onOpenChange={setOpen} onSelect={handleSelect}>
      <CommandInput placeholder="Type a command or search..." />
      <CommandList>
        <CommandEmpty />
        <CommandGroup heading="Navigation">
          <CommandItem value="search-places" onSelect={handleSelect}>
            <Search className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
            Search places
          </CommandItem>
          <CommandItem value="go-to-coordinates" onSelect={handleSelect}>
            <MapPin className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
            Go to coordinates
          </CommandItem>
        </CommandGroup>
        <CommandGroup heading="Map Controls">
          <CommandItem value="toggle-terrain" onSelect={handleSelect}>
            <Mountain className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
            Toggle terrain
          </CommandItem>
          <CommandItem value="toggle-globe" onSelect={handleSelect}>
            <Globe className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
            Toggle globe view
          </CommandItem>
          <CommandItem value="toggle-3d" onSelect={handleSelect}>
            <Box className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
            Toggle 3D mode
          </CommandItem>
          <CommandItem value="style-dark" onSelect={handleSelect}>
            <Palette className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
            Switch to dark style
          </CommandItem>
          <CommandItem value="style-light" onSelect={handleSelect}>
            <Palette className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
            Switch to light style
          </CommandItem>
          <CommandItem value="style-satellite" onSelect={handleSelect}>
            <Palette className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
            Switch to satellite style
          </CommandItem>
          <CommandItem value="reset-view" onSelect={handleSelect}>
            <RotateCcw className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
            Reset view
          </CommandItem>
        </CommandGroup>
        <CommandGroup heading="Layers">
          <CommandItem value="toggle-fire-perimeters" onSelect={handleSelect}>
            <Layers className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
            Toggle fire perimeters
          </CommandItem>
          <CommandItem value="toggle-sensors" onSelect={handleSelect}>
            <Layers className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
            Toggle sensors
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
