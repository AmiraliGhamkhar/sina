import { useEffect, useState } from "react";
import { Folder, FileText, RefreshCw } from "lucide-react";
import { workspaceApi } from "@/lib/api-endpoints";
import { useAgentCatalogStore } from "@/stores/agent-catalog-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { toast } from "@/stores/ui-store";
import { ApiError } from "@/lib/api-client";
import { HistoryTimeline } from "@/components/agent/HistoryTimeline";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";

export function WorkspacePanel() {
  const selectedId = useAgentCatalogStore((s) => s.selectedId);
  const files = useWorkspaceStore((s) => s.files);
  const edits = useWorkspaceStore((s) => s.edits);
  const isLoading = useWorkspaceStore((s) => s.isLoading);
  const fetchAll = useWorkspaceStore((s) => s.fetchAll);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);

  useEffect(() => {
    setSelectedFile(null);
    setFileContent(null);
    if (selectedId) void fetchAll(selectedId).catch((err: unknown) => {
      if ((err as ApiError)?.statusCode !== 404) {
        toast.error(err instanceof ApiError ? err.detail : "Could not load workspace.");
      }
    });
  }, [selectedId, fetchAll]);

  const reload = () => {
    if (!selectedId) return;
    void fetchAll(selectedId).catch((err: unknown) => {
      if ((err as ApiError)?.statusCode !== 404) {
        toast.error(err instanceof ApiError ? err.detail : "Could not load workspace.");
      }
    });
  };

  const openFile = async (path: string) => {
    if (!selectedId) return;
    setSelectedFile(path);
    try {
      const data = await workspaceApi.file(selectedId, path);
      setFileContent(data.content);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not read file.");
    }
  };

  if (!selectedId) {
    return <p className="p-4 text-center text-sm text-text-muted">Select an agent to view its workspace.</p>;
  }

  return (
    <div className="flex flex-col gap-4 p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-text-muted">Workspace</span>
        <Button variant="ghost" size="sm" leftIcon={<RefreshCw size={13} />} onClick={reload} isLoading={isLoading}>
          Refresh
        </Button>
      </div>

      {/* File browser */}
      <div>
        <p className="mb-1 flex items-center gap-1 text-[0.7rem] font-medium uppercase tracking-wide text-text-muted">
          <Folder size={12} /> Files
        </p>
        {isLoading && files.length === 0 ? (
          <Skeleton className="h-10 w-full" />
        ) : files.length === 0 ? (
          <p className="text-xs text-text-muted">No files yet.</p>
        ) : (
          <div className="flex flex-col gap-1">
            {files.map((f) => (
              <button
                key={f}
                onClick={() => openFile(f)}
                className={`flex items-center gap-1.5 rounded px-2 py-1 text-left font-mono text-xs hover:bg-bg-tertiary ${selectedFile === f ? "bg-bg-tertiary text-accent-primary" : "text-text-secondary"}`}
              >
                <FileText size={12} /> {f}
              </button>
            ))}
          </div>
        )}
      </div>

      {selectedFile && fileContent !== null && (
        <div>
          <p className="mb-1 text-[0.7rem] font-medium uppercase tracking-wide text-text-muted">{selectedFile}</p>
          <pre className="max-h-64 overflow-auto rounded bg-bg-primary p-2 font-mono text-xs text-text-secondary">{fileContent}</pre>
        </div>
      )}

      {/* History timeline */}
      <div>
        <p className="mb-1 text-[0.7rem] font-medium uppercase tracking-wide text-text-muted">History</p>
        <HistoryTimeline agentId={selectedId} edits={edits} />
      </div>
    </div>
  );
}
