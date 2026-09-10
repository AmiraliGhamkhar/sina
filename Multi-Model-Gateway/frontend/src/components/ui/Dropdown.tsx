import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export interface DropdownOption {
  value: string;
  label: string;
  sublabel?: string;
  dotColor?: string;
}

interface DropdownProps {
  value: string;
  options: DropdownOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  leadingIcon?: ReactNode;
  className?: string;
  menuClassName?: string;
  align?: "left" | "right";
  /** Open the menu upward (useful for bottom-anchored toolbars). */
  up?: boolean;
  size?: "sm" | "md";
  /** Transparent trigger with a coral hover fill (for the composer toolbar). */
  transparent?: boolean;
}

export function Dropdown({
  value,
  options,
  onChange,
  placeholder = "Select…",
  leadingIcon,
  className,
  menuClassName,
  align = "left",
  up = false,
  size = "md",
  transparent = false,
}: DropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const selected = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const trigger = size === "sm" ? "h-8 px-2.5 text-[0.8125rem]" : "h-10 px-3 text-sm";

  return (
    <div ref={ref} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center justify-between gap-2 rounded-lg border text-text-primary transition-colors outline-none",
          transparent
            ? "border-transparent bg-transparent hover:bg-accent-primary hover:text-white"
            : "border-border bg-bg-secondary hover:bg-bg-tertiary",
          trigger
        )}
      >
        <span className="flex min-w-0 items-center gap-2">
          {leadingIcon}
          {selected?.dotColor && (
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: selected.dotColor }}
            />
          )}
          <span className={cn("truncate", !selected && "text-text-muted")}>
            {selected ? selected.label : placeholder}
          </span>
        </span>
        <ChevronDown
          size={15}
          className={cn("shrink-0 text-text-muted transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div
          className={cn(
            "absolute z-40 min-w-full max-h-72 overflow-y-auto rounded-lg border border-border bg-bg-secondary p-1",
            "shadow-[0_12px_40px_-12px_rgba(0,0,0,0.7)] animate-fade-in",
            up ? "bottom-[calc(100%+6px)]" : "top-[calc(100%+6px)]",
            align === "right" ? "right-0" : "left-0",
            menuClassName
          )}
        >
          {options.map((opt) => {
            const active = opt.value === value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  onChange(opt.value);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                  active ? "bg-bg-tertiary text-text-primary" : "text-text-secondary hover:bg-bg-tertiary/60 hover:text-text-primary"
                )}
              >
                {opt.dotColor && (
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ backgroundColor: opt.dotColor }}
                  />
                )}
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate">{opt.label}</span>
                  {opt.sublabel && (
                    <span className="truncate text-[0.75rem] text-text-muted">
                      {opt.sublabel}
                    </span>
                  )}
                </span>
                {active && <Check size={15} className="shrink-0 text-accent-primary" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
