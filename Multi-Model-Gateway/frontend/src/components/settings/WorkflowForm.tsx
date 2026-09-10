import { useState } from "react";
import { Trash2 } from "lucide-react";
import { useWorkflowStore } from "@/stores/workflow-store";
import { toast } from "@/stores/ui-store";
import { ApiError } from "@/lib/api-client";
import type { Workflow } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

interface WorkflowFormProps {
  /** The workflow to edit, or null to create a new one. */
  workflow: Workflow | null;
  onSaved?: (workflow: Workflow) => void;
  onDeleted?: () => void;
  onCancel?: () => void;
  cancelLabel?: string;
}

function pretty(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return "";
  }
}

/**
 * Workflow editor — name, description, the ComfyUI graph (as JSON), and an
 * optional advanced param-map override. JSON is parsed/validated on save.
 * Seed from `workflow` on mount, so wrap in a `key` to reset between selections.
 */
export function WorkflowForm({ workflow, onSaved, onDeleted, onCancel, cancelLabel }: WorkflowFormProps) {
  const createWorkflow = useWorkflowStore((s) => s.createWorkflow);
  const updateWorkflow = useWorkflowStore((s) => s.updateWorkflow);
  const deleteWorkflow = useWorkflowStore((s) => s.deleteWorkflow);

  const creating = workflow === null;
  const [name, setName] = useState(workflow?.name ?? "");
  const [description, setDescription] = useState(workflow?.description ?? "");
  const [graphText, setGraphText] = useState(() => (workflow ? pretty(workflow.graph) : ""));
  const [paramMapText, setParamMapText] = useState(() =>
    workflow?.param_map ? pretty(workflow.param_map) : ""
  );
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const save = async () => {
    if (!name.trim()) {
      toast.error("Workflow name is required.");
      return;
    }

    let graph: Record<string, unknown>;
    try {
      const parsed = JSON.parse(graphText);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        toast.error("Graph must be a JSON object (ComfyUI API format).");
        return;
      }
      graph = parsed as Record<string, unknown>;
    } catch {
      toast.error("Graph isn't valid JSON.");
      return;
    }

    let param_map: Record<string, unknown> | null = null;
    if (paramMapText.trim()) {
      try {
        param_map = JSON.parse(paramMapText) as Record<string, unknown>;
      } catch {
        toast.error("Parameter map isn't valid JSON.");
        return;
      }
    }

    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        description: description.trim() || null,
        graph,
        param_map,
      };
      const saved = creating
        ? await createWorkflow(payload)
        : await updateWorkflow(workflow.id, payload);
      toast.success(creating ? "Workflow created." : "Workflow saved.");
      onSaved?.(saved);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not save workflow.");
    } finally {
      setSaving(false);
    }
  };

  const doDelete = async () => {
    if (!workflow) return;
    setDeleting(true);
    try {
      await deleteWorkflow(workflow.id);
      toast.success("Workflow deleted.");
      onDeleted?.();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not delete workflow.");
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  return (
    <div className="flex flex-col gap-5 animate-fade-in">
      <Input
        label="Workflow Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="e.g. SDXL Portrait"
      />
      <Input
        label="Description"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="What is this workflow for?"
      />
      <Textarea
        label="Graph (ComfyUI API format)"
        mono
        value={graphText}
        onChange={(e) => setGraphText(e.target.value)}
        className="min-h-[220px]"
        placeholder={'Paste a workflow exported via ComfyUI → "Save (API Format)"'}
        hint="Prompt, steps, seed, aspect ratio & batch size are injected automatically."
      />

      {/* Advanced: parameter-map override */}
      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={() => setShowAdvanced((s) => !s)}
          className="self-start text-[0.8125rem] text-text-secondary transition-colors hover:text-text-primary"
        >
          {showAdvanced ? "− Hide" : "+ Advanced"} parameter mapping
        </button>
        {showAdvanced && (
          <Textarea
            label="Parameter map (optional)"
            mono
            value={paramMapText}
            onChange={(e) => setParamMapText(e.target.value)}
            className="min-h-[120px]"
            placeholder={'{\n  "seed": ["3", "seed"]\n}'}
            hint='Override auto-detect. Each param → [node_id, input_key]. Leave blank to auto-detect.'
          />
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
        <Button variant="primary" onClick={save} isLoading={saving}>
          {creating ? "Create Workflow" : "Save"}
        </Button>
        {!creating && (
          <Button variant="danger" leftIcon={<Trash2 size={15} />} onClick={() => setConfirmDelete(true)}>
            Delete
          </Button>
        )}
        {onCancel && (
          <Button variant="ghost" onClick={onCancel}>
            {cancelLabel ?? "Cancel"}
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete workflow"
        message="Are you sure? This cannot be undone."
        confirmLabel="Delete Workflow"
        isLoading={deleting}
        onConfirm={doDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
