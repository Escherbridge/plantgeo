"use client";

import * as React from "react";
import { createPortal } from "react-dom";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { X } from "lucide-react";

interface SheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
}

function Sheet({ open, onOpenChange, children }: SheetProps) {
  React.useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onOpenChange]);

  React.useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50">{children}</div>,
    document.body
  );
}
Sheet.displayName = "Sheet";

const sheetContentVariants = cva(
  "fixed z-50 bg-[hsl(var(--card))] text-[hsl(var(--card-foreground))] shadow-xl transition-transform duration-300 ease-in-out border-[hsl(var(--border))]",
  {
    variants: {
      side: {
        left: "inset-y-0 left-0 h-full w-3/4 max-w-sm border-r animate-[slide-in-left_300ms_ease-out]",
        right:
          "inset-y-0 right-0 h-full w-3/4 max-w-sm border-l animate-[slide-in-right_300ms_ease-out]",
        bottom:
          "inset-x-0 bottom-0 w-full max-h-[85vh] border-t rounded-t-lg animate-[slide-in-bottom_300ms_ease-out]",
      },
    },
    defaultVariants: {
      side: "right",
    },
  }
);

interface SheetContentProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof sheetContentVariants> {
  onOpenChange?: (open: boolean) => void;
}

const SheetContent = React.forwardRef<HTMLDivElement, SheetContentProps>(
  ({ side = "right", className, children, onOpenChange, ...props }, ref) => {
    return (
      <>
        <div
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm animate-[fade-in_150ms_ease-out]"
          onClick={() => onOpenChange?.(false)}
          aria-hidden="true"
        />
        <div
          ref={ref}
          className={cn(sheetContentVariants({ side }), className)}
          {...props}
        >
          <div className="flex h-full flex-col overflow-y-auto p-6">
            {children}
          </div>
          {onOpenChange && (
            <button
              onClick={() => onOpenChange(false)}
              className="absolute right-4 top-4 rounded-(--radius) p-1 text-[hsl(var(--muted-foreground))] opacity-70 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
            >
              <X className="h-4 w-4" />
              <span className="sr-only">Close</span>
            </button>
          )}
        </div>
      </>
    );
  }
);
SheetContent.displayName = "SheetContent";

function SheetHeader({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("flex flex-col space-y-2 mb-4", className)}
      {...props}
    />
  );
}
SheetHeader.displayName = "SheetHeader";

function SheetTitle({
  className,
  ...props
}: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2
      className={cn(
        "text-lg font-semibold text-[hsl(var(--foreground))]",
        className
      )}
      {...props}
    />
  );
}
SheetTitle.displayName = "SheetTitle";

function SheetDescription({
  className,
  ...props
}: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p
      className={cn("text-sm text-[hsl(var(--muted-foreground))]", className)}
      {...props}
    />
  );
}
SheetDescription.displayName = "SheetDescription";

export { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription };
