"use client";

import { Briefcase, Check, HeartHandshake, Landmark, User, Users } from "lucide-react";
import { cn } from "@/lib/utils";

export type OrganizationType =
  | "nonprofit"
  | "cooperative"
  | "business"
  | "individual"
  | "government";

const ORGANIZATION_TYPES: Array<{
  value: OrganizationType;
  label: string;
  description: string;
  icon: React.ElementType;
}> = [
  {
    value: "nonprofit",
    label: "Nonprofit",
    description: "Conservation groups, land trusts, watchdogs",
    icon: HeartHandshake,
  },
  {
    value: "cooperative",
    label: "Cooperative",
    description: "Grower co-ops, shared-resource collectives",
    icon: Users,
  },
  {
    value: "business",
    label: "Business",
    description: "Contractors, consultancies, agencies",
    icon: Briefcase,
  },
  {
    value: "government",
    label: "Government",
    description: "Agencies, municipalities, tribal nations",
    icon: Landmark,
  },
  {
    value: "individual",
    label: "Individual",
    description: "Solo practitioner or independent researcher",
    icon: User,
  },
];

interface OrganizationTypePickerProps {
  value: OrganizationType | undefined;
  onChange: (value: OrganizationType) => void;
  className?: string;
}

/** Selectable grid of organization archetypes; drives copy and defaults elsewhere. */
export function OrganizationTypePicker({ value, onChange, className }: OrganizationTypePickerProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Organization type"
      className={cn("grid grid-cols-1 gap-2 sm:grid-cols-2", className)}
    >
      {ORGANIZATION_TYPES.map(({ value: optionValue, label, description, icon: Icon }) => {
        const selected = value === optionValue;
        return (
          <button
            key={optionValue}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(optionValue)}
            className={cn(
              "group relative flex items-start gap-3 rounded-lg border px-3.5 py-3 text-left transition-all",
              selected
                ? "border-emerald-500/60 bg-emerald-500/10"
                : "border-zinc-800 bg-zinc-950/40 hover:border-zinc-700 hover:bg-zinc-900"
            )}
          >
            <Icon
              className={cn(
                "mt-0.5 h-4 w-4 shrink-0 transition-colors",
                selected ? "text-emerald-400" : "text-zinc-500 group-hover:text-zinc-400"
              )}
            />
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className={cn("text-sm font-medium", selected ? "text-zinc-50" : "text-zinc-200")}>
                {label}
              </span>
              <span className="text-xs leading-snug text-zinc-500">{description}</span>
            </span>
            {selected && (
              <Check className="absolute right-2.5 top-2.5 h-3.5 w-3.5 text-emerald-400" />
            )}
          </button>
        );
      })}
    </div>
  );
}
