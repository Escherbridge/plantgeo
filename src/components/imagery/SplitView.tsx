"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { useImageryStore } from "@/stores/imagery-store";
import type { MapillaryImage } from "@/lib/server/services/mapillary";

const PanoViewer = dynamic(() => import("./PanoViewer").then((m) => m.PanoViewer), {
  ssr: false,
  loading: () => (
    <div className="w-full h-full flex items-center justify-center bg-black text-white/50 text-sm">
      Loading viewer...
    </div>
  ),
});

interface SplitViewProps {
  children: React.ReactNode;
}

export function SplitView({ children }: SplitViewProps) {
  const {
    showSplitView,
    selectedImageId,
    selectedImage,
    setSelectedImage,
    setSelectedImageId,
    closeSplitView,
  } = useImageryStore();

  const [sequenceImages, setSequenceImages] = useState<MapillaryImage[]>([]);
  const [sequenceIndex, setSequenceIndex] = useState(0);

  useEffect(() => {
    if (!selectedImageId) return;

    fetch(`/api/imagery/image?id=${encodeURIComponent(selectedImageId)}`)
      .then((r) => r.json())
      .then((img: MapillaryImage) => {
        setSelectedImage(img);
        if (img.sequenceId) {
          fetch(`/api/imagery/sequence?sequenceId=${encodeURIComponent(img.sequenceId)}`)
            .then((r) => r.json())
            .then((images: MapillaryImage[]) => {
              setSequenceImages(images);
              const idx = images.findIndex((i) => i.id === selectedImageId);
              setSequenceIndex(idx >= 0 ? idx : 0);
            })
            .catch(() => {
              setSequenceImages([img]);
              setSequenceIndex(0);
            });
        } else {
          setSequenceImages([img]);
          setSequenceIndex(0);
        }
      })
      .catch(() => {});
  }, [selectedImageId, setSelectedImage]);

  const handleNavigate = (direction: "prev" | "next") => {
    if (sequenceImages.length === 0) return;

    const nextIndex =
      direction === "next"
        ? Math.min(sequenceIndex + 1, sequenceImages.length - 1)
        : Math.max(sequenceIndex - 1, 0);

    if (nextIndex !== sequenceIndex) {
      setSequenceIndex(nextIndex);
      const nextImage = sequenceImages[nextIndex];
      setSelectedImageId(nextImage.id);
    }
  };

  if (!showSplitView) {
    return <div className="w-full h-full">{children}</div>;
  }

  const currentImage = selectedImage ?? (sequenceImages[sequenceIndex] || null);

  return (
    <div className="flex w-full h-full">
      <div className="flex-1 h-full min-w-0">{children}</div>
      <div className="w-1/2 h-full shrink-0 border-l border-[hsl(var(--border))]">
        {currentImage ? (
          <PanoViewer
            imageUrl={currentImage.thumbUrl}
            compassAngle={currentImage.compassAngle}
            sequenceId={currentImage.sequenceId}
            onNavigate={handleNavigate}
            onClose={closeSplitView}
            className="w-full h-full"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center bg-black text-white/50 text-sm">
            Loading image...
          </div>
        )}
      </div>
    </div>
  );
}

export default SplitView;
