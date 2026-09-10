import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Plus, Star } from "lucide-react";
import { useProviderStore } from "@/stores/provider-store";
import type { ProviderRole, ProviderType } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ProviderForm } from "./ProviderForm";

type View = { mode: "list" } | { mode: "edit"; id: string } | { mode: "create" };

const TYPE_LABEL: Record<ProviderType, string> = {
  openai_compatible: "OpenAI-compatible",
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  openrouter: "OpenRouter",
};

const ROLE_LABEL: Record<ProviderRole, string> = {
  local: "Local",
  cloud: "Cloud",
};

/** Providers manager (bring-your-own-key). List → edit/create via view state. */
export function ProviderPanel() {
  const { providers, isLoading, hasLoaded, fetchProviders } = useProviderStore();
  const [view, setView] = useState<View>({ mode: "list" });

  useEffect(() => {
    if (!hasLoaded) void fetchProviders();
  }, [hasLoaded, fetchProviders]);

  if (view.mode !== "list") {
    const editing = view.mode === "edit" ? providers.find((p) => p.id === view.id) ?? null : null;
    return (
      <div className="flex flex-col gap-4">
        <button
          onClick={() => setView({ mode: "list" })}
          className="flex items-center gap-1 self-start text-[0.8125rem] text-text-secondary transition-colors hover:text-text-primary"
        >
          <ChevronLeft size={15} /> All providers
        </button>
        <ProviderForm
          key={view.mode === "edit" ? view.id : "new"}
          provider={editing}
          onSaved={(p) => setView({ mode: "edit", id: p.id })}
          onDeleted={() => setView({ mode: "list" })}
          onCancel={() => setView({ mode: "list" })}
          cancelLabel="Back"
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium text-text-secondary">Providers</span>
        <Button
          variant="primary"
          size="sm"
          leftIcon={<Plus size={15} />}
          onClick={() => setView({ mode: "create" })}
        >
          New Provider
        </Button>
      </div>

      {isLoading && providers.length === 0 ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : providers.length === 0 ? (
        <p className="px-2 py-6 text-center text-sm text-text-muted">
          No providers yet. Add one to use your own API keys.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {providers.map((p) => (
            <button
              key={p.id}
              onClick={() => setView({ mode: "edit", id: p.id })}
              className="group flex flex-col gap-2 rounded-lg border border-border bg-bg-secondary/60 p-3.5 text-left transition-colors hover:bg-bg-tertiary/60"
            >
              <span className="flex flex-wrap items-center gap-1.5">
                {p.is_default && (
                  <Star size={13} className="shrink-0 text-accent-secondary" fill="currentColor" />
                )}
                <span className="truncate text-sm font-medium text-text-primary">{p.name}</span>
                <Badge className="ml-auto">{TYPE_LABEL[p.type]}</Badge>
                <Badge dotColor={p.role === "local" ? "#30A46C" : "#FFC85C"}>
                  {ROLE_LABEL[p.role]}
                </Badge>
                <Badge className={cn(p.enabled ? "text-success" : "text-text-muted")}>
                  {p.enabled ? "Enabled" : "Disabled"}
                </Badge>
                <ChevronRight
                  size={14}
                  className="shrink-0 text-text-muted opacity-0 transition-opacity group-hover:opacity-100"
                />
              </span>
              <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[0.75rem] text-text-muted">
                {p.api_key_masked && <span className="shrink-0">{p.api_key_masked}</span>}
                {p.base_url && <span className="truncate">{p.base_url}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
