"use client";

import * as React from "react";
import { X, CheckCircle, AlertCircle, AlertTriangle, Info } from "lucide-react";
import { cn } from "@/lib/utils";

type ToastVariant = "default" | "success" | "error" | "warning" | "info";

interface Toast {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
  duration: number;
}

type Listener = () => void;

let toasts: Toast[] = [];
const listeners = new Set<Listener>();

function emitChange() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: Listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return toasts;
}

function removeToast(id: string) {
  toasts = toasts.filter((t) => t.id !== id);
  emitChange();
}

let counter = 0;

interface ToastOptions {
  title: string;
  description?: string;
  variant?: ToastVariant;
  duration?: number;
}

export function toast({
  title,
  description,
  variant = "default",
  duration = 5000,
}: ToastOptions) {
  const id = String(++counter);
  const entry: Toast = { id, title, description, variant, duration };
  toasts = [...toasts, entry];
  emitChange();

  if (duration > 0) {
    setTimeout(() => removeToast(id), duration);
  }

  return id;
}

toast.success = (title: string, opts?: Omit<ToastOptions, "title" | "variant">) =>
  toast({ title, variant: "success", ...opts });
toast.error = (title: string, opts?: Omit<ToastOptions, "title" | "variant">) =>
  toast({ title, variant: "error", ...opts });
toast.warning = (title: string, opts?: Omit<ToastOptions, "title" | "variant">) =>
  toast({ title, variant: "warning", ...opts });
toast.info = (title: string, opts?: Omit<ToastOptions, "title" | "variant">) =>
  toast({ title, variant: "info", ...opts });

const variantStyles: Record<ToastVariant, { border: string; icon: React.ElementType }> = {
  default: { border: "border-l-[hsl(var(--foreground))]", icon: Info },
  success: { border: "border-l-emerald-500", icon: CheckCircle },
  error: { border: "border-l-red-500", icon: AlertCircle },
  warning: { border: "border-l-amber-500", icon: AlertTriangle },
  info: { border: "border-l-blue-500", icon: Info },
};

const iconColors: Record<ToastVariant, string> = {
  default: "text-[hsl(var(--foreground))]",
  success: "text-emerald-500",
  error: "text-red-500",
  warning: "text-amber-500",
  info: "text-blue-500",
};

function ToastItem({ toast: t, onClose }: { toast: Toast; onClose: () => void }) {
  const [visible, setVisible] = React.useState(false);
  const [exiting, setExiting] = React.useState(false);
  const { border, icon: Icon } = variantStyles[t.variant];

  React.useEffect(() => {
    const frame = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  React.useEffect(() => {
    if (t.duration > 0) {
      const fadeTime = Math.max(0, t.duration - 300);
      const timer = setTimeout(() => setExiting(true), fadeTime);
      return () => clearTimeout(timer);
    }
  }, [t.duration]);

  const handleClose = () => {
    setExiting(true);
    setTimeout(onClose, 300);
  };

  return (
    <div
      role="alert"
      className={cn(
        "pointer-events-auto flex w-80 items-start gap-3 rounded-[var(--radius)] border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4 text-[hsl(var(--card-foreground))] shadow-lg border-l-4 transition-all duration-300",
        border,
        visible && !exiting
          ? "translate-x-0 opacity-100"
          : "translate-x-full opacity-0"
      )}
    >
      <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", iconColors[t.variant])} />
      <div className="flex-1 space-y-1">
        <p className="text-sm font-semibold leading-none">{t.title}</p>
        {t.description && (
          <p className="text-sm text-[hsl(var(--muted-foreground))]">{t.description}</p>
        )}
      </div>
      <button
        onClick={handleClose}
        className="shrink-0 rounded-[var(--radius)] p-1 text-[hsl(var(--muted-foreground))] transition-colors hover:bg-[hsl(var(--muted))] hover:text-[hsl(var(--foreground))]"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

export function Toaster() {
  const currentToasts = React.useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  if (currentToasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[9999] flex flex-col-reverse gap-2">
      {currentToasts.map((t) => (
        <ToastItem key={t.id} toast={t} onClose={() => removeToast(t.id)} />
      ))}
    </div>
  );
}
