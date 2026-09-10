import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 px-6 py-12 text-center animate-fade-in",
        className
      )}
    >
      {icon && <div className="text-text-muted">{icon}</div>}
      <h3 className="text-xl text-text-primary">{title}</h3>
      {description && (
        <p className="max-w-sm text-sm text-text-secondary">{description}</p>
      )}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}
