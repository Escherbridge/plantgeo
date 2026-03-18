"use client";

import { useState, useRef, useCallback, type ReactNode } from "react";
import { cn } from "@/lib/utils";

interface BottomSheetProps {
  children: ReactNode;
  defaultSnap?: number;
  snapPoints?: number[];
}

const DEFAULT_SNAP_POINTS = [0.25, 0.5, 0.9];

export default function BottomSheet({
  children,
  defaultSnap = 0,
  snapPoints = DEFAULT_SNAP_POINTS,
}: BottomSheetProps) {
  const [snapIndex, setSnapIndex] = useState(defaultSnap);
  const isDragging = useRef(false);
  const startY = useRef(0);
  const startHeight = useRef(0);
  const sheetRef = useRef<HTMLDivElement>(null);

  const currentHeight = snapPoints[snapIndex] * 100;

  const findNearestSnap = useCallback(
    (heightPercent: number): number => {
      let nearest = 0;
      let minDist = Infinity;
      for (let i = 0; i < snapPoints.length; i++) {
        const dist = Math.abs(snapPoints[i] * 100 - heightPercent);
        if (dist < minDist) {
          minDist = dist;
          nearest = i;
        }
      }
      return nearest;
    },
    [snapPoints],
  );

  const handleDragStart = useCallback(
    (clientY: number) => {
      isDragging.current = true;
      startY.current = clientY;
      startHeight.current = currentHeight;

      const onMove = (moveY: number) => {
        if (!isDragging.current || !sheetRef.current) return;
        const vh = window.innerHeight;
        const deltaY = startY.current - moveY;
        const deltaPct = (deltaY / vh) * 100;
        const newHeight = Math.max(10, Math.min(95, startHeight.current + deltaPct));
        sheetRef.current.style.transition = "none";
        sheetRef.current.style.height = `${newHeight}vh`;
      };

      const onEnd = (endY: number) => {
        isDragging.current = false;
        if (!sheetRef.current) return;
        const vh = window.innerHeight;
        const deltaY = startY.current - endY;
        const deltaPct = (deltaY / vh) * 100;
        const finalHeight = startHeight.current + deltaPct;
        const nearest = findNearestSnap(finalHeight);
        sheetRef.current.style.transition = "";
        sheetRef.current.style.height = "";
        setSnapIndex(nearest);
        cleanup();
      };

      const onPointerMove = (e: PointerEvent) => onMove(e.clientY);
      const onPointerUp = (e: PointerEvent) => onEnd(e.clientY);
      const onTouchMove = (e: TouchEvent) => {
        e.preventDefault();
        onMove(e.touches[0].clientY);
      };
      const onTouchEnd = (e: TouchEvent) => {
        const touch = e.changedTouches[0];
        onEnd(touch.clientY);
      };

      const cleanup = () => {
        document.removeEventListener("pointermove", onPointerMove);
        document.removeEventListener("pointerup", onPointerUp);
        document.removeEventListener("touchmove", onTouchMove);
        document.removeEventListener("touchend", onTouchEnd);
      };

      document.addEventListener("pointermove", onPointerMove);
      document.addEventListener("pointerup", onPointerUp);
      document.addEventListener("touchmove", onTouchMove, { passive: false });
      document.addEventListener("touchend", onTouchEnd);
    },
    [currentHeight, findNearestSnap],
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      handleDragStart(e.clientY);
    },
    [handleDragStart],
  );

  const onTouchStart = useCallback(
    (e: React.TouchEvent) => {
      handleDragStart(e.touches[0].clientY);
    },
    [handleDragStart],
  );

  return (
    <div
      ref={sheetRef}
      className="flex flex-col rounded-t-2xl border-t shadow-xl transition-[height] duration-300 ease-out"
      style={{
        height: `${currentHeight}vh`,
        background: "var(--glass-bg)",
        backdropFilter: `blur(var(--glass-blur))`,
        WebkitBackdropFilter: `blur(var(--glass-blur))`,
        borderColor: "var(--glass-border)",
      }}
    >
      <div
        className="flex shrink-0 cursor-grab items-center justify-center py-3 active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onTouchStart={onTouchStart}
        role="slider"
        aria-label="Resize sheet"
        aria-valuemin={0}
        aria-valuemax={snapPoints.length - 1}
        aria-valuenow={snapIndex}
        tabIndex={0}
      >
        <div className="h-1 w-10 rounded-full bg-[hsl(var(--muted-foreground)/0.4)]" />
      </div>

      <div className="flex-1 overflow-y-auto overflow-x-hidden px-4 pb-4">
        {children}
      </div>
    </div>
  );
}
