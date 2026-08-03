"use client";

import { useId } from "react";
import { cn } from "@/lib/utils";
import { editorialFocusRing } from "@/components/ui/editorial/controls";

/**
 * Native select with a permanently visible label. Square, heavy-ruled, and
 * deliberately unstyled beyond the token layer so the OS listbox stays intact.
 */
export function EditorialSelectField({
  label,
  value,
  onValueChange,
  options,
  className,
}: {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  options: { value: string; label: string }[];
  className?: string;
}) {
  const fieldId = useId();

  return (
    <div className={cn("flex flex-col gap-tight", className)}>
      <label
        htmlFor={fieldId}
        className="font-editorial-label text-label text-ink-muted uppercase"
      >
        {label}
      </label>
      <select
        id={fieldId}
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
        className={cn(
          "editorial-flat rule-all-medium w-full appearance-none border-rule bg-paper px-snug py-tight font-editorial-label text-caption text-ink",
          editorialFocusRing
        )}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}
