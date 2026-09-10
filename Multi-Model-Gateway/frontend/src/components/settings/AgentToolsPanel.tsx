import { useEffect } from "react";
import { useAgentStore } from "@/stores/agent-store";
import { toast } from "@/stores/ui-store";
import { Skeleton } from "@/components/ui/Skeleton";
import { Toggle } from "@/components/ui/Toggle";

/** Tools / permissions manager for the agent right sidebar. */
export function AgentToolsPanel() {
  const { tools, isLoading, hasLoaded, fetchTools, setPermission } = useAgentStore();

  useEffect(() => {
    if (!hasLoaded) void fetchTools();
  }, [hasLoaded, fetchTools]);

  const onToggle = async (name: string, allowed: boolean) => {
    try {
      await setPermission(name, allowed);
    } catch {
      toast.error("Could not update tool permission.");
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[0.75rem] leading-relaxed text-text-muted">
        Tools the agent is allowed to call while answering. Toggle to grant or revoke access.
      </p>

      <div className="border-t border-border" />

      {isLoading && tools.length === 0 ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : tools.length === 0 ? (
        <p className="px-2 py-6 text-center text-sm text-text-muted">No tools available.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {tools.map((t) => (
            <div
              key={t.name}
              className="flex items-start gap-3 rounded-lg border border-border bg-bg-secondary/40 px-3 py-2.5"
            >
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="flex items-center gap-1.5">
                  <span className="truncate font-mono text-[0.8125rem] text-text-primary">{t.name}</span>
                  {t.first_party && (
                    <span className="shrink-0 rounded bg-bg-elevated/60 px-1.5 py-0.5 text-[0.65rem] text-text-secondary">
                      built-in
                    </span>
                  )}
                </span>
                {t.description && (
                  <span className="text-[0.75rem] leading-relaxed text-text-muted">{t.description}</span>
                )}
              </div>
              <Toggle checked={t.allowed} onChange={(v) => onToggle(t.name, v)} className="mt-0.5 shrink-0" />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
