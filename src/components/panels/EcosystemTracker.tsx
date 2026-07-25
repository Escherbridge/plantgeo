"use client";

import { useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Leaf } from "lucide-react";

type ActionType =
  | "reforestation"
  | "invasive_removal"
  | "soil_restoration"
  | "water_management";

interface EcosystemFormState {
  type: ActionType;
  description: string;
  date: string;
  area: string;
  treeCount: string;
}

const ACTION_LABELS: Record<ActionType, string> = {
  reforestation: "Reforestation",
  invasive_removal: "Invasive Species Removal",
  soil_restoration: "Soil Restoration",
  water_management: "Water Management",
};

interface EcosystemTrackerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const DEFAULT_FORM: EcosystemFormState = {
  type: "reforestation",
  description: "",
  date: new Date().toISOString().split("T")[0],
  area: "",
  treeCount: "",
};

export function EcosystemTracker({ open, onOpenChange }: EcosystemTrackerProps) {
  const [form, setForm] = useState<EcosystemFormState>(DEFAULT_FORM);

  function handleChange(
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
  ) {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Spatial observations must be location-linked and moderated before publication.
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="left" onOpenChange={onOpenChange}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Leaf className="h-5 w-5 text-green-500" />
            Ecosystem Tracker
          </SheetTitle>
        </SheetHeader>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4 mt-4">
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-[hsl(var(--foreground))]">
            Reporting is paused until this panel is connected to a selected map location
            and the moderated contribution workflow. This prevents unverified actions
            from being published as operational data.
          </div>
          <fieldset disabled className="contents opacity-60">
          <div className="flex flex-col gap-1">
            <label
              htmlFor="type"
              className="text-xs font-medium text-[hsl(var(--foreground))]"
            >
              Action Type
            </label>
            <select
              id="type"
              name="type"
              value={form.type}
              onChange={handleChange}
              required
              className="rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
            >
              {(Object.keys(ACTION_LABELS) as ActionType[]).map((key) => (
                <option key={key} value={key}>
                  {ACTION_LABELS[key]}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <label
              htmlFor="description"
              className="text-xs font-medium text-[hsl(var(--foreground))]"
            >
              Description
            </label>
            <textarea
              id="description"
              name="description"
              value={form.description}
              onChange={handleChange}
              required
              rows={3}
              placeholder="Describe the ecosystem action taken..."
              className="rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] resize-none"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label
              htmlFor="date"
              className="text-xs font-medium text-[hsl(var(--foreground))]"
            >
              Date
            </label>
            <input
              id="date"
              name="date"
              type="date"
              value={form.date}
              onChange={handleChange}
              required
              className="rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
            />
          </div>

          <div className="flex gap-3">
            <div className="flex flex-col gap-1 flex-1">
              <label
                htmlFor="area"
                className="text-xs font-medium text-[hsl(var(--foreground))]"
              >
                Area (ha)
              </label>
              <input
                id="area"
                name="area"
                type="number"
                min="0"
                step="0.1"
                value={form.area}
                onChange={handleChange}
                placeholder="0.0"
                className="rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
              />
            </div>

            <div className="flex flex-col gap-1 flex-1">
              <label
                htmlFor="treeCount"
                className="text-xs font-medium text-[hsl(var(--foreground))]"
              >
                Tree Count (optional)
              </label>
              <input
                id="treeCount"
                name="treeCount"
                type="number"
                min="0"
                step="1"
                value={form.treeCount}
                onChange={handleChange}
                placeholder="0"
                className="rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
              />
            </div>
          </div>

          <Button
            type="submit"
            disabled
            className="bg-green-600 hover:bg-green-700 text-white"
          >
            Location-linked reporting required
          </Button>
          </fieldset>
        </form>
      </SheetContent>
    </Sheet>
  );
}
