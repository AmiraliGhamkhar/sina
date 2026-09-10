import type { ReactNode } from "react";

interface TwoPanelProps {
  title: string;
  subtitle?: string;
  headerExtra?: ReactNode;
  list: ReactNode;
  detail: ReactNode;
}

/** Shared "list on the left, editor on the right" page scaffold. */
export function TwoPanel({ title, subtitle, headerExtra, list, detail }: TwoPanelProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-border px-6 py-4 max-md:pl-14">
        <div>
          <h1 className="text-2xl text-text-primary">{title}</h1>
          {subtitle && <p className="text-sm text-text-secondary">{subtitle}</p>}
        </div>
        {headerExtra}
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[300px_1fr] lg:grid-cols-[340px_1fr]">
        <div className="min-h-0 overflow-y-auto border-r border-border p-3">{list}</div>
        <div className="min-h-0 overflow-y-auto p-6">{detail}</div>
      </div>
    </div>
  );
}
