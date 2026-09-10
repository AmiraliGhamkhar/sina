import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, ChevronLeft, ChevronRight, ExternalLink } from "lucide-react";
import { useWorkflowStore } from "@/stores/workflow-store";
import type { Workflow } from "@/lib/types";
import { truncate } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { WorkflowForm } from "./WorkflowForm";

type View = { mode: "list" } | { mode: "edit"; id: string } | { mode: "create" };

/** Workflows manager for the image right sidebar (CRUD). */
export function WorkflowPanel() {
  const { workflows, isLoading, hasLoaded, fetchWorkflows } = useWorkflowStore();
  const navigate = useNavigate();
  const [view, setView] = useState<View>({ mode: "list" });

  useEffect(() => {
    if (!hasLoaded) void fetchWorkflows();
  }, [hasLoaded, fetchWorkflows]);

  if (view.mode !== "list") {
    const editing = view.mode === "edit" ? workflows.find((w) => w.id === view.id) ?? null : null;
    return (
      <div className="flex flex-col gap-4">
        <button
          onClick={() => setView({ mode: "list" })}
          className="flex items-center gap-1 self-start text-[0.8125rem] text-text-secondary transition-colors hover:text-text-primary"
        >
          <ChevronLeft size={15} /> All workflows
        </button>
        <WorkflowForm
          key={view.mode === "edit" ? view.id : "new"}
          workflow={editing}
          onSaved={(w) => setView({ mode: "edit", id: w.id })}
          onDeleted={() => setView({ mode: "list" })}
          onCancel={() => setView({ mode: "list" })}
          cancelLabel="Back"
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[0.75rem] leading-relaxed text-text-muted">
        Workflows are ComfyUI graphs. Pick one per generation from the prompt options — prompt,
        steps, seed, etc. are injected automatically.
      </p>

      <div className="border-t border-border" />

      <div className="flex items-center justify-between">
        <span className="text-[0.8125rem] font-medium text-text-secondary">Manage workflows</span>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<Plus size={15} />}
          onClick={() => setView({ mode: "create" })}
        >
          New
        </Button>
      </div>

      {isLoading && workflows.length === 0 ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : workflows.length === 0 ? (
        <p className="px-2 py-6 text-center text-sm text-text-muted">No workflows yet.</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {workflows.map((w: Workflow) => (
            <button
              key={w.id}
              onClick={() => setView({ mode: "edit", id: w.id })}
              className="group flex flex-col gap-1 rounded-lg border-l-2 border-transparent px-3 py-2.5 text-left transition-colors hover:bg-bg-tertiary/60"
            >
              <span className="flex items-center gap-1.5">
                <span className="truncate text-sm font-medium text-text-primary">{w.name}</span>
                <ChevronRight
                  size={14}
                  className="ml-auto shrink-0 text-text-muted opacity-0 transition-opacity group-hover:opacity-100"
                />
              </span>
              {w.description && (
                <span className="truncate text-[0.75rem] text-text-muted">
                  {truncate(w.description, 52)}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      <button
        onClick={() => navigate("/workflows")}
        className="mt-1 flex items-center gap-1.5 self-start text-[0.8125rem] text-accent-primary transition-opacity hover:opacity-80"
      >
        Open full editor <ExternalLink size={13} />
      </button>
    </div>
  );
}
