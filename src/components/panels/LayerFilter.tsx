"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { useLayerStore } from "@/stores/layer-store";

type Operator = "=" | "!=" | ">" | "<" | "contains";

interface FilterRule {
  property: string;
  operator: Operator;
  value: string;
}

interface LayerFilterProps {
  layerId: string;
  layerName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const OPERATORS: { value: Operator; label: string }[] = [
  { value: "=", label: "=" },
  { value: "!=", label: "!=" },
  { value: ">", label: ">" },
  { value: "<", label: "<" },
  { value: "contains", label: "contains" },
];

function ruleToExpression(rule: FilterRule): unknown[] {
  const { property, operator, value } = rule;
  const num = Number(value);
  const val = isNaN(num) ? value : num;

  switch (operator) {
    case "=":
      return ["==", ["get", property], val];
    case "!=":
      return ["!=", ["get", property], val];
    case ">":
      return [">", ["get", property], val];
    case "<":
      return ["<", ["get", property], val];
    case "contains":
      return ["in", value, ["get", property]];
  }
}

export function LayerFilter({ layerId, layerName, open, onOpenChange }: LayerFilterProps) {
  const { filterExpressions, setFilterExpressions } = useLayerStore();
  const [rules, setRules] = useState<FilterRule[]>([]);
  const [property, setProperty] = useState("");
  const [operator, setOperator] = useState<Operator>("=");
  const [value, setValue] = useState("");

  function addRule() {
    if (!property.trim()) return;
    setRules([...rules, { property: property.trim(), operator, value }]);
    setProperty("");
    setValue("");
  }

  function removeRule(index: number) {
    setRules(rules.filter((_, i) => i !== index));
  }

  function handleApply() {
    const expressions = rules.map(ruleToExpression);
    setFilterExpressions(layerId, expressions);
    onOpenChange(false);
  }

  function handleClear() {
    setRules([]);
    setFilterExpressions(layerId, []);
    onOpenChange(false);
  }

  const existing = filterExpressions.get(layerId) ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent onOpenChange={onOpenChange}>
        <DialogHeader>
          <DialogTitle>Filter: {layerName}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-2">
          {existing.length > 0 && (
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              {existing.length} active filter{existing.length !== 1 ? "s" : ""} applied
            </p>
          )}

          <div className="flex gap-2">
            <input
              value={property}
              onChange={(e) => setProperty(e.target.value)}
              placeholder="Property"
              className="flex-1 rounded-(--radius) border border-[hsl(var(--input))] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
            />
            <select
              value={operator}
              onChange={(e) => setOperator(e.target.value as Operator)}
              className="rounded-(--radius) border border-[hsl(var(--input))] bg-[hsl(var(--card))] px-2 py-1.5 text-sm outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
            >
              {OPERATORS.map((op) => (
                <option key={op.value} value={op.value}>
                  {op.label}
                </option>
              ))}
            </select>
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="Value"
              className="w-24 rounded-(--radius) border border-[hsl(var(--input))] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
            />
            <Button size="icon" variant="outline" onClick={addRule}>
              <Plus className="h-4 w-4" />
            </Button>
          </div>

          {rules.length > 0 && (
            <ul className="flex flex-col gap-1">
              {rules.map((rule, i) => (
                <li
                  key={i}
                  className="flex items-center justify-between rounded-(--radius) bg-[hsl(var(--secondary))] px-3 py-1.5 text-sm"
                >
                  <span>
                    <span className="font-medium">{rule.property}</span>{" "}
                    <span className="text-[hsl(var(--muted-foreground))]">{rule.operator}</span>{" "}
                    {rule.value}
                  </span>
                  <button onClick={() => removeRule(i)}>
                    <X className="h-3.5 w-3.5 text-[hsl(var(--muted-foreground))]" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClear}>
            Clear
          </Button>
          <Button onClick={handleApply} disabled={rules.length === 0}>
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
