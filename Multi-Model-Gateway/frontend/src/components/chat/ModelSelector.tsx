import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Check } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { useModelStore } from "@/stores/model-store";
import type { Provider } from "@/lib/types";
import { cn, PROVIDER_DOT } from "@/lib/utils";

/** "openai/gpt-4o" → "gpt-4o" for compact display. */
function short(id: string): string {
  const i = id.lastIndexOf("/");
  return i >= 0 ? id.slice(i + 1) : id;
}

interface ModelItem {
  id: string;
  label: string;
}

interface ProviderRowProps {
  name: string;
  dot: string;
  active: boolean;
  models: ModelItem[];
  loading: boolean;
  selectedModel: string | null;
  onHover: () => void;
  onSelectProvider: () => void;
  onSelectModel: (id: string) => void;
}

function ProviderRow({
  name,
  dot,
  active,
  models,
  loading,
  selectedModel,
  onHover,
  onSelectProvider,
  onSelectModel,
}: ProviderRowProps) {
  return (
    <div className="group/row relative" onMouseEnter={onHover}>
      <button
        type="button"
        onClick={onSelectProvider}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
          active
            ? "bg-bg-tertiary text-text-primary"
            : "text-text-secondary hover:bg-bg-tertiary/60 hover:text-text-primary"
        )}
      >
        <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: dot }} />
        <span className="flex-1 truncate">{name}</span>
        <ChevronRight size={14} className="shrink-0 text-text-muted" />
      </button>

      {/* Models flyout — opens to the RIGHT, bottom-anchored so it grows upward
          and stays inside the viewport (height capped to 60vh, scrolls if longer). */}
      <div className="invisible absolute bottom-0 left-full z-50 max-h-[60vh] w-56 overflow-y-auto rounded-lg border border-border bg-bg-secondary p-1 opacity-0 shadow-[0_12px_40px_-12px_rgba(0,0,0,0.7)] transition-opacity duration-100 group-hover/row:visible group-hover/row:opacity-100">
        {loading ? (
          <p className="px-2.5 py-3 text-sm text-text-muted">Loading…</p>
        ) : models.length === 0 ? (
          <p className="px-2.5 py-3 text-sm text-text-muted">No models found</p>
        ) : (
          models.map((m) => {
            const sel = m.id === selectedModel;
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => onSelectModel(m.id)}
                title={m.id}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[0.8125rem] transition-colors",
                  sel
                    ? "bg-bg-tertiary text-text-primary"
                    : "text-text-secondary hover:bg-bg-tertiary/60 hover:text-text-primary"
                )}
              >
                <span className="flex-1 truncate">{m.label}</span>
                {sel && <Check size={13} className="shrink-0 text-accent-primary" />}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

export function ModelSelector() {
  const provider = useChatStore((s) => s.provider);
  const model = useChatStore((s) => s.model);
  const selectModel = useChatStore((s) => s.setModelSelection);

  const localModels = useModelStore((s) => s.localModels);
  const orModels = useModelStore((s) => s.openrouterModels);
  const loadingLocal = useModelStore((s) => s.loadingLocal);
  const loadingOR = useModelStore((s) => s.loadingOpenRouter);
  const fetchLocal = useModelStore((s) => s.fetchLocal);
  const fetchOR = useModelStore((s) => s.fetchOpenRouter);

  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const choose = (p: Provider, m: string) => {
    selectModel(p, m);
    setOpen(false);
  };

  const triggerLabel =
    model !== "auto"
      ? short(model)
      : provider === "local"
      ? "Local"
      : provider === "openrouter"
      ? "OpenRouter"
      : "Auto";

  return (
    <div ref={ref} className="relative w-[150px]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex h-8 w-full items-center justify-between gap-2 rounded-lg border border-transparent bg-transparent px-2.5 text-[0.8125rem] text-text-primary transition-colors hover:bg-accent-primary hover:text-white"
        title={model !== "auto" ? model : undefined}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ backgroundColor: PROVIDER_DOT[provider] ?? PROVIDER_DOT.auto }}
          />
          <span className="truncate">{triggerLabel}</span>
        </span>
        <ChevronDown
          size={15}
          className={cn("shrink-0 text-text-muted transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div className="absolute bottom-[calc(100%+6px)] left-0 z-40 w-full rounded-lg border border-border bg-bg-secondary p-1 shadow-[0_12px_40px_-12px_rgba(0,0,0,0.7)] animate-fade-in">
          {/* Auto — no specific model, let the gateway route */}
          <button
            type="button"
            onClick={() => choose("auto", "auto")}
            className={cn(
              "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
              provider === "auto"
                ? "bg-bg-tertiary text-text-primary"
                : "text-text-secondary hover:bg-bg-tertiary/60 hover:text-text-primary"
            )}
          >
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: PROVIDER_DOT.auto }} />
            <span className="flex-1">Auto</span>
            {provider === "auto" && <Check size={14} className="shrink-0 text-accent-primary" />}
          </button>

          <ProviderRow
            name="Local"
            dot={PROVIDER_DOT.local}
            active={provider === "local"}
            models={localModels.map((m) => ({ id: m.id, label: short(m.id) }))}
            loading={loadingLocal}
            selectedModel={provider === "local" && model !== "auto" ? model : null}
            onHover={fetchLocal}
            onSelectProvider={() => choose("local", "auto")}
            onSelectModel={(id) => choose("local", id)}
          />

          <ProviderRow
            name="OpenRouter"
            dot={PROVIDER_DOT.openrouter}
            active={provider === "openrouter"}
            models={orModels.map((m) => ({ id: m.id, label: m.name || short(m.id) }))}
            loading={loadingOR}
            selectedModel={provider === "openrouter" && model !== "auto" ? model : null}
            onHover={fetchOR}
            onSelectProvider={() => choose("openrouter", "auto")}
            onSelectModel={(id) => choose("openrouter", id)}
          />
        </div>
      )}
    </div>
  );
}
