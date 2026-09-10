import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface BadgeProps {
  children: ReactNode;
  className?: string;
  dotColor?: string;
}

export function Badge({ children, className, dotColor }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border bg-bg-tertiary/70",
        "px-2 py-0.5 text-[0.75rem] font-medium text-text-secondary whitespace-nowrap",
        className
      )}
    >
      {dotColor && (
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: dotColor }}
        />
      )}
      {children}
    </span>
  );
}
