import { useEffect, useRef } from "react";
import { Bot, AlertTriangle, Plus } from "lucide-react";
import { useAgent } from "@/hooks/use-agent";
import { useAgentCatalogStore } from "@/stores/agent-catalog-store";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { ChatInput } from "@/components/chat/ChatInput";
import { ToolStepCard } from "@/components/agent/ToolStepCard";
import { Spinner } from "@/components/ui/Spinner";
import { Button } from "@/components/ui/Button";

export default function AgentPage() {
  const { turns, isStreaming, error, send, cancel, reset } = useAgent();
  const { agents, selectedId, select } = useAgentCatalogStore();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "auto" });
  }, [turns, isStreaming]);

  const empty = turns.length === 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-border bg-bg-secondary/60 px-5 py-3 max-md:pl-14">
        <div className="flex items-center gap-2">
          <Bot size={18} className="text-accent-primary" />
          <span className="text-base text-text-primary">Agent</span>
          <span className="hidden text-[0.8125rem] text-text-muted sm:inline">· tool-using assistant</span>
          {agents.length > 0 && (
            <select
              value={selectedId ?? ""}
              onChange={(e) => select(e.target.value || null)}
              className="ml-2 rounded border border-border bg-bg-primary px-2 py-1 text-xs text-text-primary"
            >
              <option value="">General</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} v{a.version}
                </option>
              ))}
            </select>
          )}
        </div>
        {!empty && (
          <Button variant="ghost" size="sm" leftIcon={<Plus size={15} />} onClick={reset}>
            New
          </Button>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {empty ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center animate-fade-in">
            <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-bg-tertiary">
              <Bot size={26} className="text-accent-primary" />
            </span>
            <h2 className="text-2xl text-text-primary">Ask the agent</h2>
            <p className="max-w-md text-sm text-text-secondary">
              It can call tools to look things up before answering. Manage which tools are
              allowed in the panel on the right.
            </p>
          </div>
        ) : (
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
            {turns.map((turn, i) => {
              const active = isStreaming && i === turns.length - 1;
              return (
                <div key={i} className="flex flex-col gap-3">
                  <MessageBubble role="user" content={turn.user} />

                  {turn.steps.length > 0 && (
                    <div className="flex flex-col gap-1.5">
                      {turn.steps.map((s) => (
                        <ToolStepCard key={s.id} step={s} />
                      ))}
                    </div>
                  )}

                  {turn.answer ? (
                    <MessageBubble role="assistant" content={turn.answer} />
                  ) : active ? (
                    <div className="flex items-center gap-2 px-1 text-[0.8125rem] text-text-muted">
                      <Spinner size={14} /> Working…
                    </div>
                  ) : null}
                </div>
              );
            })}

            {error && (
              <div className="flex items-center gap-2 rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger animate-fade-in">
                <AlertTriangle size={16} />
                {error}
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <ChatInput onSend={(c) => send(c)} onCancel={cancel} isStreaming={isStreaming} />
    </div>
  );
}
