import type { ReactNode } from "react";
import { Brain, Eye, Wrench } from "lucide-react";
import { cn } from "@/lib/utils";

interface CapabilityBadgeProps {
  capability: string;
}

// Known capability pills get an icon + accent color; anything else falls back
// to a plain neutral badge (backend capability strings are a best-effort tag
// scrape and are matched case-insensitively here).
const KNOWN: Record<string, { icon: ReactNode; cls: string }> = {
  vision: { icon: <Eye size={13} />, cls: "bg-accent-secondary/15 text-accent-secondary" },
  "tool use": { icon: <Wrench size={13} />, cls: "bg-[#3b82f6]/15 text-[#93c5fd]" },
  reasoning: { icon: <Brain size={13} />, cls: "bg-success/15 text-success" },
};

export function CapabilityBadge({ capability }: CapabilityBadgeProps) {
  const known = KNOWN[capability.toLowerCase()];
  if (!known) {
    return (
      <span className="inline-flex items-center whitespace-nowrap rounded-full border border-border bg-bg-tertiary/70 px-2 py-0.5 text-[0.75rem] font-medium text-text-secondary">
        {capability}
      </span>
    );
  }
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[0.75rem] font-medium",
        known.cls
      )}
    >
      {known.icon}
      {capability}
    </span>
  );
}
