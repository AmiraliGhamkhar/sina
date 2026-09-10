import { useEffect, useMemo, useState } from "react";
import { Plus, Workflow as WorkflowIcon } from "lucide-react";
import { useWorkflowStore } from "@/stores/workflow-store";
import type { Workflow } from "@/lib/types";
import { cn, truncate } from "@/lib/utils";
import { TwoPanel } from "@/components/layout/TwoPanel";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { WorkflowForm } from "@/components/settings/WorkflowForm";

export default function WorkflowsPage() {
  const { workflows, isLoading, hasLoaded, fetchWorkflows } = useWorkflowStore();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!hasLoaded) void fetchWorkflows();
  }, [hasLoaded, fetchWorkflows]);

  const selected = useMemo(
    () => workflows.find((w) => w.id === selectedId) ?? null,
    [workflows, selectedId]
  );

  const startCreate = () => {
    setCreating(true);
    setSelectedId(null);
  };

  const selectWorkflow = (w: Workflow) => {
    setCreating(false);
    setSelectedId(w.id);
  };

  const editing = creating || !!selected;

  const list = (
    <div className="flex flex-col gap-2">
      <Button variant="primary" fullWidth leftIcon={<Plus size={16} />} onClick={startCreate}>
        Create New Workflow
      </Button>

      {isLoading && workflows.length === 0 ? (
        <div className="flex flex-col gap-2 pt-1">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : workflows.length === 0 ? (
        <p className="px-2 py-6 text-center text-sm text-text-muted">No workflows yet.</p>
      ) : (
        workflows.map((w) => {
          const active = w.id === selectedId && !creating;
          return (
            <button
              key={w.id}
              onClick={() => selectWorkflow(w)}
              className={cn(
                "flex flex-col gap-1 rounded-lg border-l-2 px-3 py-2.5 text-left transition-colors",
                active
                  ? "border-accent-primary bg-bg-tertiary"
                  : "border-transparent hover:bg-bg-tertiary/60"
              )}
            >
              <span className="truncate text-sm font-medium text-text-primary">{w.name}</span>
              {w.description && (
                <span className="truncate text-[0.75rem] text-text-muted">
                  {truncate(w.description, 50)}
                </span>
              )}
            </button>
          );
        })
      )}
    </div>
  );

  const detail = !editing ? (
    <EmptyState
      icon={<WorkflowIcon size={40} strokeWidth={1.5} />}
      title="No workflow selected"
      description="Select a workflow to edit, or create one by pasting a ComfyUI API-format graph."
      action={
        <Button variant="primary" leftIcon={<Plus size={16} />} onClick={startCreate}>
          Create Workflow
        </Button>
      }
    />
  ) : (
    <div className="mx-auto flex max-w-2xl flex-col">
      <WorkflowForm
        key={creating ? "new" : selected?.id}
        workflow={creating ? null : selected}
        onSaved={(w) => {
          setCreating(false);
          setSelectedId(w.id);
        }}
        onDeleted={() => {
          setSelectedId(null);
          setCreating(false);
        }}
        onCancel={() => {
          setCreating(false);
          setSelectedId(null);
        }}
      />
    </div>
  );

  return (
    <TwoPanel
      title="Workflows"
      subtitle="ComfyUI graphs for image generation"
      list={list}
      detail={detail}
    />
  );
}
