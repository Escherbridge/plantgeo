"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

interface SliderProps {
  min?: number;
  max?: number;
  step?: number;
  value: number;
  onValueChange: (value: number) => void;
  className?: string;
  disabled?: boolean;
  /** Forwarded so a caption elsewhere can point at this control with `htmlFor`. */
  id?: string;
  /**
   * A slider MUST carry one of these. A bare `<input type="range">` announces as an unnamed
   * slider, which is what every use of this component did before the layer tree needed
   * seventeen of them on one screen.
   */
  "aria-label"?: string;
  "aria-labelledby"?: string;
  /**
   * What the value MEANS, spoken. Without it a 0-to-1 opacity reads as "0.6"; with it, "60
   * percent". Never fold the reading into the name -- the name must stay stable as the thumb
   * moves.
   */
  "aria-valuetext"?: string;
  /** Extra description (a percentage caption, a unit) that is not part of the name. */
  "aria-describedby"?: string;
}

/**
 * Injected once per document rather than once per instance.
 *
 * This block used to render inside the component body, so N sliders emitted N identical
 * `<style>` elements -- and the layer tree puts one slider on every switched-on layer.
 *
 * `height` deliberately sets only a floor via the `plantgeo-slider` class being overridable:
 * the rule below is what a bare slider looks like, and any caller may raise the box with a
 * `className` height (the layer rows use `max-sm:h-11` for the 44px mobile tap target the
 * rest of this codebase enforces). Tailwind utilities win over this rule's single class
 * specificity, so no `!important` and no inline style is needed.
 */
const SLIDER_STYLE_ELEMENT_ID = "plantgeo-slider-styles";

const sliderStyles = `
  .plantgeo-slider {
    -webkit-appearance: none;
    appearance: none;
    height: 6px;
    border-radius: 9999px;
    outline: none;
    cursor: pointer;
  }
  .plantgeo-slider:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
  .plantgeo-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: hsl(var(--primary));
    border: 2px solid hsl(var(--background));
    cursor: pointer;
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.3);
    transition: box-shadow 150ms;
  }
  .plantgeo-slider::-webkit-slider-thumb:hover {
    box-shadow: 0 0 0 4px hsl(var(--primary) / 0.3);
  }
  .plantgeo-slider:focus-visible::-webkit-slider-thumb {
    box-shadow: 0 0 0 4px hsl(var(--ring) / 0.5);
  }
  .plantgeo-slider:disabled::-webkit-slider-thumb {
    cursor: not-allowed;
  }
  .plantgeo-slider::-moz-range-thumb {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: hsl(var(--primary));
    border: 2px solid hsl(var(--background));
    cursor: pointer;
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.3);
    transition: box-shadow 150ms;
  }
  .plantgeo-slider::-moz-range-thumb:hover {
    box-shadow: 0 0 0 4px hsl(var(--primary) / 0.3);
  }
  .plantgeo-slider:focus-visible::-moz-range-thumb {
    box-shadow: 0 0 0 4px hsl(var(--ring) / 0.5);
  }
  .plantgeo-slider:disabled::-moz-range-thumb {
    cursor: not-allowed;
  }
`;

/**
 * Appends the stylesheet the first time any slider mounts and leaves it in place.
 *
 * Guarded on the element id rather than a module flag so a second bundle instance, or a
 * remount after a fast-refresh, still resolves to one `<style>`. It is never removed: the
 * rules are static, so tearing them down on the last unmount would only risk a flash of an
 * unstyled control when the next one mounts.
 */
function useSliderStyles(): void {
  React.useEffect(() => {
    if (typeof document === "undefined") return;
    if (document.getElementById(SLIDER_STYLE_ELEMENT_ID) !== null) return;
    const style = document.createElement("style");
    style.id = SLIDER_STYLE_ELEMENT_ID;
    style.textContent = sliderStyles;
    document.head.appendChild(style);
  }, []);
}

function Slider({
  min = 0,
  max = 100,
  step = 1,
  value,
  onValueChange,
  className,
  disabled = false,
  id,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
  "aria-valuetext": ariaValueText,
  "aria-describedby": ariaDescribedBy,
}: SliderProps) {
  useSliderStyles();

  const percentage = ((value - min) / (max - min)) * 100;

  const trackBackground = `linear-gradient(to right, hsl(var(--primary)) ${percentage}%, hsl(var(--secondary)) ${percentage}%)`;

  return (
    <input
      type="range"
      id={id}
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      aria-valuetext={ariaValueText}
      aria-describedby={ariaDescribedBy}
      onChange={(e) => onValueChange(Number(e.target.value))}
      className={cn("plantgeo-slider w-full", className)}
      style={{ background: trackBackground }}
    />
  );
}
Slider.displayName = "Slider";

export { Slider };
export type { SliderProps };
