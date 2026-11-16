import { editorialFocusRing } from "@/components/ui/editorial";
import { cn } from "@/lib/utils";

/** Chrome's own hardware-acceleration help. No PlantGeo guide exists for this yet. */
const HARDWARE_ACCELERATION_HELP = "https://support.google.com/chrome/answer/6258784";

/**
 * Sits in the map slot when the canvas cannot be created. The same sentences
 * are used on the home overlay and on `/plan/*`.
 */
export function MapWebGLFallback({ fill = true }: { fill?: boolean }) {
  return (
    <div
      role="status"
      className={cn(
        "flex items-center justify-center bg-ink px-comfortable text-paper",
        fill ? "h-full w-full" : "min-h-64 w-full",
      )}
    >
      <div className="max-w-[40ch] text-center">
        <h2 className="font-editorial-display text-heading font-semibold text-paper">
          This browser can&apos;t draw the map.
        </h2>
        <p className="mt-snug font-editorial-text text-body text-paper">
          PlantGeo needs WebGL to show the 3D map, and it&apos;s turned off or unavailable here. The
          rest of this page still works. Turning on hardware acceleration in your browser settings
          usually fixes it.
        </p>
        <a
          href={HARDWARE_ACCELERATION_HELP}
          target="_blank"
          rel="noreferrer noopener"
          className={cn(
            "mt-comfortable inline-block font-editorial-text text-body text-paper underline decoration-accent decoration-2 underline-offset-4",
            editorialFocusRing,
          )}
        >
          How to turn on hardware acceleration
        </a>
      </div>
    </div>
  );
}
