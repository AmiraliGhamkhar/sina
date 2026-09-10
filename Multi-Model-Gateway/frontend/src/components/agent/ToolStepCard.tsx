import { useState } from "react";
import { ChevronDown, Wrench, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AgentStep } from "@/hooks/use-agent";
import { DiffView } from "@/components/agent/DiffView";
import { parseFileEditResult, shouldRenderDiff } from "@/lib/agent-events";

function prettyJson(raw: string | undefined): string {
  if (!raw) return "";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function extractPatch(content: string): string | null {
  return parseFileEditResult(content)?.patch ?? null;
}

/** Collapsible inline card for one agent tool step (call + result). */
export function ToolStepCard({ step }: { step: AgentStep }) {
  const [open, setOpen] = useState(false);
  const done = step.content !== undefined;

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-bg-secondary/60">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-bg-tertiary/40"
      >
        <Wrench size={14} className="shrink-0 text-accent-secondary" />
        <span className="truncate font-mono text-[0.8125rem] text-text-primary">{step.name}</span>
        {done ? (
          <span className="text-[0.7rem] text-text-muted">done</span>
        ) : (
          <span className="flex items-center gap-1 text-[0.7rem] text-text-muted">
            <Loader2 size={11} className="animate-spin" /> running
          </span>
        )}
        <ChevronDown
          size={14}
          className={cn("ml-auto shrink-0 text-text-muted transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div className="flex flex-col gap-2 border-t border-border px-3 py-2">
          {step.arguments && (
            <div>
              <p className="mb-1 text-[0.7rem] uppercase tracking-wide text-text-muted">Arguments</p>
              <pre className="overflow-x-auto rounded bg-bg-primary p-2 font-mono text-[0.75rem] leading-relaxed text-text-secondary">
                {prettyJson(step.arguments)}
              </pre>
            </div>
          )}
          {step.content !== undefined && (
            <div>
              <p className="mb-1 text-[0.7rem] uppercase tracking-wide text-text-muted">Result</p>
              {shouldRenderDiff(step.name, step.content) ? (
                <DiffView patch={extractPatch(step.content) ?? step.content} />
              ) : (
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-bg-primary p-2 font-mono text-[0.75rem] leading-relaxed text-text-secondary">
                  {step.content}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
