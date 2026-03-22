"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Slider } from "@/components/ui/slider";

interface TimeSliderProps {
  min: number;
  max: number;
  value: number;
  onValueChange: (value: number) => void;
  speed?: number;
  formatLabel?: (value: number) => string;
  className?: string;
}

export function TimeSlider({
  min,
  max,
  value,
  onValueChange,
  speed = 1,
  formatLabel,
  className,
}: TimeSliderProps) {
  const [playing, setPlaying] = useState(false);
  const rafRef = useRef<number | null>(null);
  const lastTimeRef = useRef<number | null>(null);
  const currentRef = useRef(value);

  currentRef.current = value;

  const tick = useCallback(
    (timestamp: number) => {
      if (lastTimeRef.current !== null) {
        const delta = (timestamp - lastTimeRef.current) * speed;
        const next = currentRef.current + delta;
        if (next >= max) {
          onValueChange(min);
          setPlaying(false);
          lastTimeRef.current = null;
          return;
        }
        onValueChange(next);
      }
      lastTimeRef.current = timestamp;
      rafRef.current = requestAnimationFrame(tick);
    },
    [min, max, speed, onValueChange]
  );

  useEffect(() => {
    if (playing) {
      rafRef.current = requestAnimationFrame(tick);
    } else {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      lastTimeRef.current = null;
    }
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
      }
    };
  }, [playing, tick]);

  return (
    <div className={`flex flex-col gap-2 ${className ?? ""}`}>
      <div className="flex items-center gap-3">
        <button
          onClick={() => setPlaying((p) => !p)}
          className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-primary-foreground text-sm font-medium transition-opacity hover:opacity-80"
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? "⏸" : "▶"}
        </button>
        <span className="min-w-[80px] text-right text-xs tabular-nums text-muted-foreground">
          {formatLabel ? formatLabel(value) : Math.round(value)}
        </span>
      </div>
      <Slider
        min={min}
        max={max}
        step={(max - min) / 1000}
        value={value}
        onValueChange={(v) => {
          onValueChange(v);
        }}
      />
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{formatLabel ? formatLabel(min) : min}</span>
        <span>{formatLabel ? formatLabel(max) : max}</span>
      </div>
    </div>
  );
}
