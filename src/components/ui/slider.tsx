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
}

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

function Slider({
  min = 0,
  max = 100,
  step = 1,
  value,
  onValueChange,
  className,
  disabled = false,
}: SliderProps) {
  const percentage = ((value - min) / (max - min)) * 100;

  const trackBackground = `linear-gradient(to right, hsl(var(--primary)) ${percentage}%, hsl(var(--secondary)) ${percentage}%)`;

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: sliderStyles }} />
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onValueChange(Number(e.target.value))}
        className={cn("plantgeo-slider w-full", className)}
        style={{ background: trackBackground }}
      />
    </>
  );
}
Slider.displayName = "Slider";

export { Slider };
export type { SliderProps };
