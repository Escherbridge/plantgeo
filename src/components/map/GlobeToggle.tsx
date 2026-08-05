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
      // size="icon" is 40px; grown to the 44px mobile minimum here, not in the shared
      // button.tsx, since that variant is used well beyond this toolbar.
      className="max-sm:h-11 max-sm:w-11"
      onClick={toggleGlobe}
      title={isGlobeView ? "Switch to flat map" : "Switch to globe"}
    >
      <Globe />
    </Button>
  );
}
