import { useEffect, useMemo, useState } from "react";
import { Plus, Star, LayoutTemplate } from "lucide-react";
import { useTemplateStore } from "@/stores/template-store";
import type { PromptTemplate } from "@/lib/types";
import { cn, truncate } from "@/lib/utils";
import { TwoPanel } from "@/components/layout/TwoPanel";
import { TemplateForm } from "@/components/settings/TemplateForm";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";

export default function TemplatesPage() {
  const { templates, isLoading, hasLoaded, fetchTemplates } = useTemplateStore();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!hasLoaded) void fetchTemplates();
  }, [hasLoaded, fetchTemplates]);

  const selected = useMemo(
    () => templates.find((t) => t.id === selectedId) ?? null,
    [templates, selectedId]
  );

  const selectTemplate = (t: PromptTemplate) => {
    setCreating(false);
    setSelectedId(t.id);
  };

  const startCreate = () => {
    setCreating(true);
    setSelectedId(null);
  };

  const reset = () => {
    setCreating(false);
    setSelectedId(null);
  };

  const editing = creating || !!selected;

  const list = (
    <div className="flex flex-col gap-2">
      <Button variant="primary" fullWidth leftIcon={<Plus size={16} />} onClick={startCreate}>
        Create New Template
      </Button>

      {isLoading && templates.length === 0 ? (
        <div className="flex flex-col gap-2 pt-1">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : templates.length === 0 ? (
        <p className="px-2 py-6 text-center text-sm text-text-muted">No templates yet.</p>
      ) : (
        templates.map((t) => {
          const isDefault = t.name === "Default SDXL Template";
          const active = t.id === selectedId && !creating;
          return (
            <button
              key={t.id}
              onClick={() => selectTemplate(t)}
              className={cn(
                "flex flex-col gap-1 rounded-lg border-l-2 px-3 py-2.5 text-left transition-colors",
                active
                  ? "border-accent-primary bg-bg-tertiary"
                  : "border-transparent hover:bg-bg-tertiary/60"
              )}
            >
              <span className="flex items-center gap-1.5">
                {isDefault && <Star size={13} className="text-accent-secondary" fill="currentColor" />}
                <span className="truncate text-sm font-medium text-text-primary">{t.name}</span>
              </span>
              {t.description && (
                <span className="truncate text-[0.75rem] text-text-muted">
                  {truncate(t.description, 60)}
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
      icon={<LayoutTemplate size={40} strokeWidth={1.5} />}
      title="No template selected"
      description="Select a template to edit, or create one to customize how prompts are rewritten for image generation."
      action={
        <Button variant="primary" leftIcon={<Plus size={16} />} onClick={startCreate}>
          Create Template
        </Button>
      }
    />
  ) : (
    <div className="mx-auto max-w-2xl">
      <TemplateForm
        key={creating ? "new" : selectedId}
        template={creating ? null : selected}
        onSaved={(t) => {
          setCreating(false);
          setSelectedId(t.id);
        }}
        onDeleted={reset}
        onCancel={reset}
      />
    </div>
  );

  return (
    <TwoPanel
      title="Prompt Templates"
      subtitle="SDXL prompt rewriting structures"
      list={list}
      detail={detail}
    />
  );
}
