import { useEffect, useMemo, useState } from "react";
import { Plus, Bot, Globe } from "lucide-react";
import { useAgentCatalogStore } from "@/stores/agent-catalog-store";
import type { Agent } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TwoPanel } from "@/components/layout/TwoPanel";
import { AgentForm } from "@/components/agents/AgentForm";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";

export default function AgentsPage() {
  const { agents, isLoading, hasLoaded, fetchAgents } = useAgentCatalogStore();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!hasLoaded) void fetchAgents();
  }, [hasLoaded, fetchAgents]);

  const selected = useMemo(() => agents.find((a) => a.id === selectedId) ?? null, [agents, selectedId]);

  const startCreate = () => {
    setCreating(true);
    setSelectedId(null);
  };
  const reset = () => {
    setCreating(false);
    setSelectedId(null);
  };
  const selectAgent = (a: Agent) => {
    setCreating(false);
    setSelectedId(a.id);
  };

  const editing = creating || !!selected;

  const list = (
    <div className="flex flex-col gap-2">
      <Button variant="primary" fullWidth leftIcon={<Plus size={16} />} onClick={startCreate}>
        Create Agent
      </Button>
      {isLoading && agents.length === 0 ? (
        <div className="flex flex-col gap-2 pt-1">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : agents.length === 0 ? (
        <p className="px-2 py-6 text-center text-sm text-text-muted">No agents yet.</p>
      ) : (
        agents.map((a) => {
          const active = a.id === selectedId && !creating;
          return (
            <button
              key={a.id}
              onClick={() => selectAgent(a)}
              className={cn(
                "flex flex-col gap-1 rounded-lg border-l-2 px-3 py-2.5 text-left transition-colors",
                active ? "border-accent-primary bg-bg-tertiary" : "border-transparent hover:bg-bg-tertiary/60"
              )}
            >
              <span className="flex items-center gap-1.5">
                <Bot size={13} className={active ? "text-accent-primary" : "text-text-muted"} />
                <span className="truncate text-sm font-medium text-text-primary">{a.name}</span>
                {a.is_public && <Globe size={11} className="ml-auto text-text-muted" />}
                <span className="rounded bg-bg-elevated/60 px-1.5 py-0.5 font-mono text-[0.7rem] text-text-secondary">v{a.version}</span>
              </span>
              {a.description && <span className="truncate text-[0.75rem] text-text-muted">{a.description}</span>}
            </button>
          );
        })
      )}
    </div>
  );

  const detail = !editing ? (
    <EmptyState
      icon={<Bot size={40} strokeWidth={1.5} />}
      title="No agent selected"
      description="Select an agent to edit, or create one — use Smart Suggest to draft it from a goal."
      action={
        <Button variant="primary" leftIcon={<Plus size={16} />} onClick={startCreate}>
          Create Agent
        </Button>
      }
    />
  ) : (
    <div className="mx-auto max-w-2xl">
      <AgentForm
        key={creating ? "new" : selectedId}
        agent={creating ? null : selected}
        onSaved={(a) => {
          setCreating(false);
          setSelectedId(a.id);
        }}
        onCancel={reset}
      />
    </div>
  );

  return <TwoPanel title="Agents" subtitle="User-created assistants — instructions + toolsets" list={list} detail={detail} />;
}
