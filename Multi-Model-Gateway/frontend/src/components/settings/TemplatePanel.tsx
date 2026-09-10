import { useEffect, useState } from "react";
import { Plus, Star, ChevronLeft, ChevronRight } from "lucide-react";
import { useTemplateStore } from "@/stores/template-store";
import type { PromptTemplate } from "@/lib/types";
import { truncate } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { TemplateForm } from "./TemplateForm";

type View = { mode: "list" } | { mode: "edit"; id: string } | { mode: "create" };

/** Prompt-templates manager for the image right sidebar (CRUD). */
export function TemplatePanel() {
  const { templates, isLoading, hasLoaded, fetchTemplates } = useTemplateStore();
  const [view, setView] = useState<View>({ mode: "list" });

  useEffect(() => {
    if (!hasLoaded) void fetchTemplates();
  }, [hasLoaded, fetchTemplates]);

  if (view.mode !== "list") {
    const editing =
      view.mode === "edit" ? templates.find((t) => t.id === view.id) ?? null : null;
    return (
      <div className="flex flex-col gap-4">
        <button
          onClick={() => setView({ mode: "list" })}
          className="flex items-center gap-1 self-start text-[0.8125rem] text-text-secondary transition-colors hover:text-text-primary"
        >
          <ChevronLeft size={15} /> All templates
        </button>
        <TemplateForm
          key={view.mode === "edit" ? view.id : "new"}
          template={editing}
          onSaved={(t) => setView({ mode: "edit", id: t.id })}
          onDeleted={() => setView({ mode: "list" })}
          onCancel={() => setView({ mode: "list" })}
          cancelLabel="Back"
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[0.75rem] leading-relaxed text-text-muted">
        Templates structure how prompts are rewritten for image generation. Pick one per
        generation from the prompt options.
      </p>

      <div className="border-t border-border" />

      <div className="flex items-center justify-between">
        <span className="text-[0.8125rem] font-medium text-text-secondary">Manage templates</span>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<Plus size={15} />}
          onClick={() => setView({ mode: "create" })}
        >
          New
        </Button>
      </div>

      {isLoading && templates.length === 0 ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : templates.length === 0 ? (
        <p className="px-2 py-6 text-center text-sm text-text-muted">No templates yet.</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {templates.map((t: PromptTemplate) => {
            const isDefault = t.name === "Default SDXL Template";
            return (
              <button
                key={t.id}
                onClick={() => setView({ mode: "edit", id: t.id })}
                className="group flex flex-col gap-1 rounded-lg border-l-2 border-transparent px-3 py-2.5 text-left transition-colors hover:bg-bg-tertiary/60"
              >
                <span className="flex items-center gap-1.5">
                  {isDefault && (
                    <Star size={13} className="shrink-0 text-accent-secondary" fill="currentColor" />
                  )}
                  <span className="truncate text-sm font-medium text-text-primary">{t.name}</span>
                  <ChevronRight
                    size={14}
                    className="ml-auto shrink-0 text-text-muted opacity-0 transition-opacity group-hover:opacity-100"
                  />
                </span>
                {t.description && (
                  <span className="truncate text-[0.75rem] text-text-muted">
                    {truncate(t.description, 52)}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
