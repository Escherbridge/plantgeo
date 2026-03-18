"use client";

import { Globe } from "lucide-react";
import { useMapStore } from "@/stores/map-store";
import { Button } from "@/components/ui/button";

export default function GlobeToggle() {
  const { isGlobeView, toggleGlobe } = useMapStore();

  return (
    <Button
      variant={isGlobeView ? "default" : "ghost"}
      size="icon"
      onClick={toggleGlobe}
      title={isGlobeView ? "Switch to flat map" : "Switch to globe"}
    >
      <Globe />
    </Button>
  );
}
