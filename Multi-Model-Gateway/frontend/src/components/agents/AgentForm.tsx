import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { useAgentCatalogStore } from "@/stores/agent-catalog-store";
import { useAgentStore } from "@/stores/agent-store";
import { toast } from "@/stores/ui-store";
import { ApiError } from "@/lib/api-client";
import type { Agent } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { Toggle } from "@/components/ui/Toggle";

interface AgentFormProps {
  agent: Agent | null;
  onSaved?: (a: Agent) => void;
  onCancel?: () => void;
}

// Suggest state is local to the form — no global store needed.
export function AgentForm({ agent, onSaved, onCancel }: AgentFormProps) {
  const creating = agent === null;
  const createAgent = useAgentCatalogStore((s) => s.createAgent);
  const updateAgent = useAgentCatalogStore((s) => s.updateAgent);
  const suggestAction = useAgentCatalogStore((s) => s.suggest);
  const tools = useAgentStore((s) => s.tools);

  const [name, setName] = useState(agent?.name ?? "");
  const [description, setDescription] = useState(agent?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(agent?.system_prompt ?? "");
  const [allowedTools, setAllowedTools] = useState<string[]>(agent?.allowed_tools ?? []);
  const [isPublic, setIsPublic] = useState(agent?.is_public ?? false);
  const [saving, setSaving] = useState(false);
  const [goal, setGoal] = useState("");
  const [suggesting, setSuggesting] = useState(false);

  // Keep form in sync when agent prop changes (e.g. selecting different agent)
  useEffect(() => {
    setName(agent?.name ?? "");
    setDescription(agent?.description ?? "");
    setSystemPrompt(agent?.system_prompt ?? "");
    setAllowedTools(agent?.allowed_tools ?? []);
    setIsPublic(agent?.is_public ?? false);
  }, [agent?.id]); // eslint-disable-line react-hooks\exhaustive-deps

  const suggest = async () => {
    if (!goal.trim()) {
      toast.error("Enter a goal for Smart Suggest.");
      return;
    }
    setSuggesting(true);
    try {
      const res = await suggestAction(goal.trim());
      setName(res.name);
      setDescription(res.description);
      setSystemPrompt(res.system_prompt);
      setAllowedTools(res.suggested_tools);
      toast.success("Draft applied — review and save.");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Suggest failed.");
    } finally {
      setSuggesting(false);
    }
  };

  const save = async () => {
    if (!name.trim()) {
      toast.error("Agent name is required.");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        description: description.trim() || null,
        system_prompt: systemPrompt.trim() || null,
        allowed_tools: allowedTools,
        is_public: isPublic,
      };
      const saved = creating ? await createAgent(payload as any) : await updateAgent(agent.id, payload as any);
      toast.success(creating ? "Agent created." : "Agent saved.");
      onSaved?.(saved);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not save agent.");
    } finally {
      setSaving(false);
    }
  };

  const toggleTool = (tool: string, on: boolean) => {
    setAllowedTools((prev) => (on ? [...prev, tool] : prev.filter((t) => t !== tool)));
  };

  return (
    <div className="flex flex-col gap-5 animate-fade-in">
      {/* Smart Suggest — goal → LLM draft */}
      <div className="rounded-lg border border-border bg-bg-secondary/40 p-4">
        <p className="mb-2 text-[0.75rem] font-medium uppercase tracking-wide text-text-muted">Smart Suggest</p>
        <div className="flex gap-2">
          <Input
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="e.g. SEO GEO optimizer for Shopify stores"
            className="flex-1"
          />
          <Button variant="secondary" onClick={suggest} isLoading={suggesting} leftIcon={<Sparkles size={15} />}>
            Suggest
          </Button>
        </div>
        <p className="mt-1 text-[0.7rem] text-text-muted">Describe the goal; the AI drafts the agent for you.</p>
      </div>

      <Input label="Agent Name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. SEO GEO Optimizer" />

      <Textarea
        label="Description"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="One-line summary shown in marketplace…"
        className="min-h-[60px]"
      />

      <Textarea
        label="Instructions (system prompt)"
        mono
        value={systemPrompt}
        onChange={(e) => setSystemPrompt(e.target.value)}
        placeholder="You are an SEO auditor that…"
        className="min-h-[140px]"
      />

      {/* Tool selection */}
      <div>
        <p className="mb-2 text-sm font-medium text-text-primary">Allowed Tools</p>
        {tools.length === 0 ? (
          <p className="text-xs text-text-muted">No tools available. Configure tools in Settings → Agent Tools.</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {tools.map((t) => (
              <label key={t.name} className="flex items-center gap-2 rounded-lg border border-border bg-bg-secondary/40 px-3 py-2">
                <input
                  type="checkbox"
                  checked={allowedTools.includes(t.name)}
                  onChange={(e) => toggleTool(t.name, e.target.checked)}
                  className="h-4 w-4 rounded border-border"
                />
                <span className="flex-1 font-mono text-[0.8125rem] text-text-primary">{t.name}</span>
                <span className="text-[0.7rem] text-text-muted">{t.first_party ? "built-in" : "mcp"}</span>
              </label>
            ))}
          </div>
        )}
      </div>

      <label className="flex items-center gap-2">
        <Toggle checked={isPublic} onChange={setIsPublic} />
        <span className="text-sm text-text-primary">Public (visible in Marketplace)</span>
      </label>

      <div className="flex flex-wrap gap-2 border-t border-border pt-4">
        <Button variant="primary" onClick={save} isLoading={saving}>
          {creating ? "Create Agent" : "Save"}
        </Button>
        {onCancel && (
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </div>
  );
}
