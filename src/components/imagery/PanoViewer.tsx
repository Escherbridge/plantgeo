"use client";

import { useEffect, useRef, useCallback } from "react";
import { ChevronLeft, ChevronRight, X, Compass } from "lucide-react";
import { cn } from "@/lib/utils";

interface PanoViewerProps {
  imageUrl: string;
  compassAngle: number;
  sequenceId: string;
  onNavigate: (direction: "prev" | "next") => void;
  onClose?: () => void;
  className?: string;
}

export function PanoViewer({
  imageUrl,
  compassAngle,
  sequenceId: _sequenceId,
  onNavigate,
  onClose,
  className,
}: PanoViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const yawRef = useRef(0);
  const pitchRef = useRef(0);
  const isDraggingRef = useRef(false);
  const lastMouseRef = useRef({ x: 0, y: 0 });
  const imageRef = useRef<HTMLImageElement | null>(null);
  const animFrameRef = useRef<number>(0);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imageRef.current;
    if (!canvas || !img || !img.complete) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { width, height } = canvas;
    const imgW = img.naturalWidth;
    const imgH = img.naturalHeight;

    const fovH = Math.PI / 2;
    const fovV = (fovH * height) / width;

    const yaw = yawRef.current;
    const pitch = pitchRef.current;

    const srcX = ((yaw / (2 * Math.PI)) * imgW + imgW) % imgW;
    const srcY = ((pitch / Math.PI + 0.5) * imgH);

    const srcW = (fovH / (2 * Math.PI)) * imgW;
    const srcH = (fovV / Math.PI) * imgH;

    ctx.clearRect(0, 0, width, height);

    const x1 = srcX - srcW / 2;
    const x2 = x1 + srcW;

    if (x1 >= 0 && x2 <= imgW) {
      ctx.drawImage(img, x1, srcY - srcH / 2, srcW, srcH, 0, 0, width, height);
    } else {
      const leftW = ((x1 % imgW) + imgW) % imgW;
      const rightW = srcW - (imgW - leftW);

      if (leftW > 0) {
        ctx.drawImage(
          img,
          leftW, srcY - srcH / 2, imgW - leftW, srcH,
          0, 0, ((imgW - leftW) / srcW) * width, height
        );
      }
      if (rightW > 0) {
        const dstX = ((imgW - leftW) / srcW) * width;
        ctx.drawImage(
          img,
          0, srcY - srcH / 2, rightW, srcH,
          dstX, 0, (rightW / srcW) * width, height
        );
      }
    }
  }, []);

  useEffect(() => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = imageUrl;
    img.onload = () => {
      imageRef.current = img;
      yawRef.current = 0;
      pitchRef.current = 0;
      draw();
    };
    imageRef.current = img;

    return () => {
      img.onload = null;
    };
  }, [imageUrl, draw]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const resizeObserver = new ResizeObserver(() => {
      canvas.width = container.clientWidth;
      canvas.height = container.clientHeight;
      draw();
    });
    resizeObserver.observe(container);

    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;

    return () => resizeObserver.disconnect();
  }, [draw]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    isDraggingRef.current = true;
    lastMouseRef.current = { x: e.clientX, y: e.clientY };
  }, []);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!isDraggingRef.current) return;

      const dx = e.clientX - lastMouseRef.current.x;
      const dy = e.clientY - lastMouseRef.current.y;
      lastMouseRef.current = { x: e.clientX, y: e.clientY };

      const canvas = canvasRef.current;
      if (!canvas) return;

      yawRef.current -= (dx / canvas.width) * (Math.PI / 2);
      pitchRef.current -= (dy / canvas.height) * (Math.PI / 4);
      pitchRef.current = Math.max(-Math.PI / 3, Math.min(Math.PI / 3, pitchRef.current));

      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = requestAnimationFrame(draw);
    },
    [draw]
  );

  const handleMouseUp = useCallback(() => {
    isDraggingRef.current = false;
  }, []);

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    if (e.touches.length === 1) {
      isDraggingRef.current = true;
      lastMouseRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    }
  }, []);

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (!isDraggingRef.current || e.touches.length !== 1) return;

      const dx = e.touches[0].clientX - lastMouseRef.current.x;
      const dy = e.touches[0].clientY - lastMouseRef.current.y;
      lastMouseRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY };

      const canvas = canvasRef.current;
      if (!canvas) return;

      yawRef.current -= (dx / canvas.width) * (Math.PI / 2);
      pitchRef.current -= (dy / canvas.height) * (Math.PI / 4);
      pitchRef.current = Math.max(-Math.PI / 3, Math.min(Math.PI / 3, pitchRef.current));

      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = requestAnimationFrame(draw);
    },
    [draw]
  );

  const handleTouchEnd = useCallback(() => {
    isDraggingRef.current = false;
  }, []);

  useEffect(() => {
    return () => cancelAnimationFrame(animFrameRef.current);
  }, []);

  return (
    <div
      ref={containerRef}
      className={cn("relative w-full h-full bg-black overflow-hidden", className)}
    >
      <canvas
        ref={canvasRef}
        className="w-full h-full cursor-grab active:cursor-grabbing"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
      />

      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-3">
        <button
          onClick={() => onNavigate("prev")}
          className="flex items-center justify-center w-10 h-10 rounded-full bg-black/60 text-white border border-white/20 hover:bg-black/80 transition-colors"
          aria-label="Previous image"
        >
          <ChevronLeft size={20} />
        </button>
        <button
          onClick={() => onNavigate("next")}
          className="flex items-center justify-center w-10 h-10 rounded-full bg-black/60 text-white border border-white/20 hover:bg-black/80 transition-colors"
          aria-label="Next image"
        >
          <ChevronRight size={20} />
        </button>
      </div>

      <div className="absolute top-4 right-4 flex items-center gap-2">
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-black/60 text-white border border-white/20">
          <Compass size={14} />
          <span className="text-xs font-mono">{Math.round(compassAngle)}°</span>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="flex items-center justify-center w-8 h-8 rounded-md bg-black/60 text-white border border-white/20 hover:bg-black/80 transition-colors"
            aria-label="Close viewer"
          >
            <X size={16} />
          </button>
        )}
      </div>
    </div>
  );
}

export default PanoViewer;
