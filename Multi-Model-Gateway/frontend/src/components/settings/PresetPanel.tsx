import { useEffect, useState } from "react";
import { Plus, Star, ChevronLeft, ChevronRight } from "lucide-react";
import { usePresetStore } from "@/stores/preset-store";
import { useChatStore } from "@/stores/chat-store";
import type { Preset } from "@/lib/types";
import { cn, truncate } from "@/lib/utils";
import { Dropdown } from "@/components/ui/Dropdown";
import type { DropdownOption } from "@/components/ui/Dropdown";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { PresetForm } from "./PresetForm";

type View = { mode: "list" } | { mode: "edit"; id: string } | { mode: "create" };

/** Presets manager for the chat right sidebar (active selector + CRUD). */
export function PresetPanel() {
  const { presets, isLoading, hasLoaded, fetchPresets } = usePresetStore();
  const presetId = useChatStore((s) => s.presetId);
  const setPresetId = useChatStore((s) => s.setPresetId);

  const [view, setView] = useState<View>({ mode: "list" });

  useEffect(() => {
    if (!hasLoaded) void fetchPresets();
  }, [hasLoaded, fetchPresets]);

  // Active-preset selector — mirrors the composer dropdown (same store key).
  const activeOptions: DropdownOption[] = [
    { value: "", label: "Default preset" },
    ...presets
      .filter((p) => p.name !== "Default")
      .map((p) => ({ value: p.id, label: p.name, sublabel: `temp ${p.temperature}` })),
  ];

  if (view.mode !== "list") {
    const editing = view.mode === "edit" ? presets.find((p) => p.id === view.id) ?? null : null;
    return (
      <div className="flex flex-col gap-4">
        <button
          onClick={() => setView({ mode: "list" })}
          className="flex items-center gap-1 self-start text-[0.8125rem] text-text-secondary transition-colors hover:text-text-primary"
        >
          <ChevronLeft size={15} /> All presets
        </button>
        <PresetForm
          key={view.mode === "edit" ? view.id : "new"}
          preset={editing}
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
      {/* Active preset for the current chat */}
      <div className="flex flex-col gap-1.5">
        <label className="text-[0.8125rem] font-medium text-text-secondary">Active preset</label>
        <Dropdown
          value={presetId ?? ""}
          options={activeOptions}
          onChange={(v) => setPresetId(v || null)}
          className="w-full"
        />
        <p className="text-[0.75rem] text-text-muted">Applied to new messages in this chat.</p>
      </div>

      <div className="border-t border-border" />

      {/* Manage presets */}
      <div className="flex items-center justify-between">
        <span className="text-[0.8125rem] font-medium text-text-secondary">Manage presets</span>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<Plus size={15} />}
          onClick={() => setView({ mode: "create" })}
        >
          New
        </Button>
      </div>

      {isLoading && presets.length === 0 ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : presets.length === 0 ? (
        <p className="px-2 py-6 text-center text-sm text-text-muted">No presets yet.</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {presets.map((p: Preset) => {
            const isDefault = p.name === "Default";
            const isActive = presetId === p.id;
            return (
              <button
                key={p.id}
                onClick={() => setView({ mode: "edit", id: p.id })}
                className={cn(
                  "group flex flex-col gap-1 rounded-lg border-l-2 px-3 py-2.5 text-left transition-colors",
                  isActive
                    ? "border-accent-primary bg-bg-tertiary/60"
                    : "border-transparent hover:bg-bg-tertiary/60"
                )}
              >
                <span className="flex items-center gap-1.5">
                  {isDefault && (
                    <Star size={13} className="shrink-0 text-accent-secondary" fill="currentColor" />
                  )}
                  <span className="truncate text-sm font-medium text-text-primary">{p.name}</span>
                  <span className="ml-auto shrink-0 rounded bg-bg-elevated/60 px-1.5 py-0.5 font-mono text-[0.7rem] text-text-secondary">
                    temp {p.temperature}
                  </span>
                  <ChevronRight
                    size={14}
                    className="shrink-0 text-text-muted opacity-0 transition-opacity group-hover:opacity-100"
                  />
                </span>
                {p.system_prompt && (
                  <span className="truncate text-[0.75rem] text-text-muted">
                    {truncate(p.system_prompt, 48)}
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
