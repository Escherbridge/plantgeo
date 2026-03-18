"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { Dialog, DialogContent } from "./dialog";

interface CommandContextValue {
  search: string;
  onSearchChange: (value: string) => void;
  selectedIndex: number;
  setSelectedIndex: React.Dispatch<React.SetStateAction<number>>;
  filteredCount: React.MutableRefObject<number>;
  onSelect: (value: string) => void;
  registerItem: (id: string, label: string) => void;
  unregisterItem: (id: string) => void;
}

const CommandContext = React.createContext<CommandContextValue | null>(null);

function useCommandContext() {
  const ctx = React.useContext(CommandContext);
  if (!ctx) throw new Error("Command compound components must be used within <Command>");
  return ctx;
}

interface CommandProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "onSelect"> {
  onSelect?: (value: string) => void;
}

const Command = React.forwardRef<HTMLDivElement, CommandProps>(
  ({ className, onSelect, children, ...props }, ref) => {
    const [search, setSearch] = React.useState("");
    const [selectedIndex, setSelectedIndex] = React.useState(0);
    const filteredCount = React.useRef(0);
    const itemsRef = React.useRef<Map<string, string>>(new Map());

    const registerItem = React.useCallback((id: string, label: string) => {
      itemsRef.current.set(id, label);
    }, []);

    const unregisterItem = React.useCallback((id: string) => {
      itemsRef.current.delete(id);
    }, []);

    const handleSelect = React.useCallback(
      (value: string) => {
        onSelect?.(value);
      },
      [onSelect]
    );

    React.useEffect(() => {
      setSelectedIndex(0);
    }, [search]);

    const handleKeyDown = (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => (i + 1) % Math.max(filteredCount.current, 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) =>
          (i - 1 + Math.max(filteredCount.current, 1)) % Math.max(filteredCount.current, 1)
        );
      } else if (e.key === "Enter") {
        e.preventDefault();
        const items = document.querySelectorAll("[data-command-item]");
        const visibleItems = Array.from(items).filter(
          (el) => !el.hasAttribute("data-hidden")
        );
        const selected = visibleItems[selectedIndex];
        if (selected) {
          const value = selected.getAttribute("data-value") ?? "";
          handleSelect(value);
        }
      }
    };

    return (
      <CommandContext.Provider
        value={{
          search,
          onSearchChange: setSearch,
          selectedIndex,
          setSelectedIndex,
          filteredCount,
          onSelect: handleSelect,
          registerItem,
          unregisterItem,
        }}
      >
        <div
          ref={ref}
          onKeyDown={handleKeyDown}
          className={cn(
            "flex w-full flex-col overflow-hidden rounded-(--radius) border border-[hsl(var(--border))] bg-[hsl(var(--popover))] text-[hsl(var(--popover-foreground))]",
            className
          )}
          {...props}
        >
          {children}
        </div>
      </CommandContext.Provider>
    );
  }
);
Command.displayName = "Command";

interface CommandDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect?: (value: string) => void;
  children: React.ReactNode;
}

function CommandDialog({ open, onOpenChange, onSelect, children }: CommandDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="overflow-hidden p-0 shadow-xl" onOpenChange={onOpenChange}>
        <Command
          onSelect={(value) => {
            onSelect?.(value);
            onOpenChange(false);
          }}
        >
          {children}
        </Command>
      </DialogContent>
    </Dialog>
  );
}

const CommandInput = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => {
  const { search, onSearchChange } = useCommandContext();

  return (
    <div className="flex items-center border-b border-[hsl(var(--border))] px-3">
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="mr-2 h-4 w-4 shrink-0 text-[hsl(var(--muted-foreground))]"
      >
        <circle cx="11" cy="11" r="8" />
        <path d="m21 21-4.3-4.3" />
      </svg>
      <input
        ref={ref}
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        className={cn(
          "flex h-11 w-full bg-transparent py-3 text-sm text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))] outline-none disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
        {...props}
      />
    </div>
  );
});
CommandInput.displayName = "CommandInput";

const CommandList = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn("max-h-[300px] overflow-y-auto overflow-x-hidden p-1", className)}
        {...props}
      />
    );
  }
);
CommandList.displayName = "CommandList";

function CommandEmpty({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const { search, filteredCount } = useCommandContext();

  if (!search || filteredCount.current > 0) return null;

  return (
    <div
      className={cn("py-6 text-center text-sm text-[hsl(var(--muted-foreground))]", className)}
      {...props}
    >
      {children ?? "No results found."}
    </div>
  );
}

interface CommandGroupProps extends React.HTMLAttributes<HTMLDivElement> {
  heading?: string;
}

function CommandGroup({ heading, className, children, ...props }: CommandGroupProps) {
  return (
    <div
      role="group"
      className={cn("overflow-hidden p-1 text-[hsl(var(--foreground))]", className)}
      {...props}
    >
      {heading && (
        <div className="px-2 py-1.5 text-xs font-medium text-[hsl(var(--muted-foreground))]">
          {heading}
        </div>
      )}
      {children}
    </div>
  );
}

interface CommandItemProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "onSelect"> {
  value?: string;
  disabled?: boolean;
  onSelect?: (value: string) => void;
}

function CommandItem({
  value,
  disabled,
  onSelect: onItemSelect,
  className,
  children,
  ...props
}: CommandItemProps) {
  const ctx = useCommandContext();
  const itemRef = React.useRef<HTMLDivElement>(null);
  const id = React.useId();
  const itemValue = value ?? (typeof children === "string" ? children : id);
  const label = typeof children === "string" ? children : itemValue;

  React.useEffect(() => {
    ctx.registerItem(id, label);
    return () => ctx.unregisterItem(id);
  }, [id, label, ctx.registerItem, ctx.unregisterItem]);

  const matchesSearch =
    !ctx.search ||
    label.toLowerCase().includes(ctx.search.toLowerCase());

  React.useEffect(() => {
    if (!itemRef.current) return;
    const items = document.querySelectorAll("[data-command-item]:not([data-hidden])");
    const arr = Array.from(items);
    ctx.filteredCount.current = arr.length;
  });

  if (!matchesSearch) {
    return <div ref={itemRef} data-command-item data-hidden data-value={itemValue} className="hidden" />;
  }

  const handleSelect = () => {
    if (disabled) return;
    onItemSelect?.(itemValue);
    ctx.onSelect(itemValue);
  };

  const allVisible = document.querySelectorAll
    ? Array.from(document.querySelectorAll("[data-command-item]:not([data-hidden])"))
    : [];
  const myIndex = allVisible.indexOf(itemRef.current!);
  const isSelected = myIndex === ctx.selectedIndex;

  return (
    <div
      ref={itemRef}
      data-command-item
      data-value={itemValue}
      role="option"
      aria-selected={isSelected}
      aria-disabled={disabled}
      onClick={handleSelect}
      className={cn(
        "relative flex cursor-pointer select-none items-center rounded-[calc(var(--radius)-2px)] px-2 py-1.5 text-sm outline-none transition-colors",
        isSelected && "bg-[hsl(var(--accent))] text-[hsl(var(--accent-foreground))]",
        disabled && "pointer-events-none opacity-50",
        !isSelected && "hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))]",
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export {
  Command,
  CommandDialog,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
};
