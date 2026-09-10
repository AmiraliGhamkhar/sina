import { RotateCcw } from "lucide-react";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { toast } from "@/stores/ui-store";
import { ApiError } from "@/lib/api-client";
import type { FileEdit } from "@/lib/types";
import { DiffView } from "@/components/agent/DiffView";
import { Button } from "@/components/ui/Button";

interface HistoryTimelineProps {
  agentId: string;
  edits: FileEdit[];
}

export function HistoryTimeline({ agentId, edits }: HistoryTimelineProps) {
  const undo = useWorkspaceStore((s) => s.undo);

  const handleUndo = async (editId: string) => {
    try {
      await undo(agentId, editId);
      toast.success("Undone.");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Undo failed.");
    }
  };

  if (edits.length === 0) {
    return <p className="text-xs text-text-muted">No edits yet.</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      {edits.map((e) => (
        <div key={e.id} className="rounded border border-border bg-bg-secondary/40 p-2">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate font-mono text-xs text-text-primary">{e.path}</span>
            <Button variant="ghost" size="sm" leftIcon={<RotateCcw size={12} />} onClick={() => handleUndo(e.id)}>
              Undo
            </Button>
          </div>
          <p className="text-[0.65rem] text-text-muted">
            {new Date(e.created_at).toLocaleString()} · {e.store}
            {e.tool_call_id ? ` · ${e.tool_call_id.slice(0, 8)}` : ""}
          </p>
          <div className="mt-1">
            <DiffView patch={e.patch} />
          </div>
        </div>
      ))}
    </div>
  );
}
