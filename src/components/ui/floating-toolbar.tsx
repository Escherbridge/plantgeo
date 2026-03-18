import * as React from "react";
import { cn } from "@/lib/utils";

interface FloatingToolbarProps {
  children: React.ReactNode;
  className?: string;
  position?: "top" | "bottom";
}

const FloatingToolbar = React.forwardRef<HTMLDivElement, FloatingToolbarProps>(
  ({ children, className, position = "top", ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(
          "absolute left-1/2 z-10 -translate-x-1/2",
          "flex items-center gap-1 rounded-xl px-2 py-1.5",
          "bg-[var(--glass-bg)] border border-[var(--glass-border)]",
          "shadow-[var(--shadow-lg)]",
          "[backdrop-filter:blur(var(--glass-blur))]",
          position === "top" ? "top-4" : "bottom-4",
          className
        )}
        {...props}
      >
        {children}
      </div>
    );
  }
);
FloatingToolbar.displayName = "FloatingToolbar";

interface FloatingToolbarItemProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  icon: React.ReactNode;
  label?: string;
  active?: boolean;
}

const FloatingToolbarItem = React.forwardRef<
  HTMLButtonElement,
  FloatingToolbarItemProps
>(({ icon, label, active, className, ...props }, ref) => {
  return (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium",
        "text-[hsl(var(--foreground))] transition-colors",
        "hover:bg-[hsl(var(--muted))] hover:text-[hsl(var(--primary))]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]",
        "disabled:pointer-events-none disabled:opacity-50",
        active && "bg-[hsl(var(--muted))] text-[hsl(var(--primary))]",
        className
      )}
      {...props}
    >
      <span className="[&_svg]:size-4 [&_svg]:shrink-0">{icon}</span>
      {label && <span>{label}</span>}
    </button>
  );
});
FloatingToolbarItem.displayName = "FloatingToolbarItem";

export { FloatingToolbar, FloatingToolbarItem };
export type { FloatingToolbarProps, FloatingToolbarItemProps };
