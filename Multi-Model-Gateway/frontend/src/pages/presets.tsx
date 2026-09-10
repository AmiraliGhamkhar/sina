import { useEffect, useMemo, useState } from "react";
import { Plus, Star, SlidersHorizontal } from "lucide-react";
import { usePresetStore } from "@/stores/preset-store";
import type { Preset } from "@/lib/types";
import { cn, truncate } from "@/lib/utils";
import { TwoPanel } from "@/components/layout/TwoPanel";
import { PresetForm } from "@/components/settings/PresetForm";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";

export default function PresetsPage() {
  const { presets, isLoading, hasLoaded, fetchPresets } = usePresetStore();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!hasLoaded) void fetchPresets();
  }, [hasLoaded, fetchPresets]);

  const selected = useMemo(
    () => presets.find((p) => p.id === selectedId) ?? null,
    [presets, selectedId]
  );

  const selectPreset = (p: Preset) => {
    setCreating(false);
    setSelectedId(p.id);
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

  // ---- list panel ----
  const list = (
    <div className="flex flex-col gap-2">
      <Button variant="primary" fullWidth leftIcon={<Plus size={16} />} onClick={startCreate}>
        Create New Preset
      </Button>

      {isLoading && presets.length === 0 ? (
        <div className="flex flex-col gap-2 pt-1">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : presets.length === 0 ? (
        <p className="px-2 py-6 text-center text-sm text-text-muted">No presets yet.</p>
      ) : (
        presets.map((p) => {
          const isDefault = p.name === "Default";
          const active = p.id === selectedId && !creating;
          return (
            <button
              key={p.id}
              onClick={() => selectPreset(p)}
              className={cn(
                "flex flex-col gap-1 rounded-lg border-l-2 px-3 py-2.5 text-left transition-colors",
                active
                  ? "border-accent-primary bg-bg-tertiary"
                  : "border-transparent hover:bg-bg-tertiary/60"
              )}
            >
              <span className="flex items-center gap-1.5">
                {isDefault && <Star size={13} className="text-accent-secondary" fill="currentColor" />}
                <span className="truncate text-sm font-medium text-text-primary">{p.name}</span>
                <span className="ml-auto rounded bg-bg-elevated/60 px-1.5 py-0.5 font-mono text-[0.7rem] text-text-secondary">
                  temp {p.temperature}
                </span>
              </span>
              {p.system_prompt && (
                <span className="truncate text-[0.75rem] text-text-muted">
                  {truncate(p.system_prompt, 50)}
                </span>
              )}
            </button>
          );
        })
      )}
    </div>
  );

  // ---- detail panel ----
  const detail = !editing ? (
    <EmptyState
      icon={<SlidersHorizontal size={40} strokeWidth={1.5} />}
      title="No preset selected"
      description="Select a preset to edit, or create a new one to customize your AI's behavior."
      action={
        <Button variant="primary" leftIcon={<Plus size={16} />} onClick={startCreate}>
          Create Preset
        </Button>
      }
    />
  ) : (
    <div className="mx-auto max-w-2xl">
      <PresetForm
        key={creating ? "new" : selectedId}
        preset={creating ? null : selected}
        onSaved={(p) => {
          setCreating(false);
          setSelectedId(p.id);
        }}
        onDeleted={reset}
        onCancel={reset}
      />
    </div>
  );

  return <TwoPanel title="Presets" subtitle="Saved model parameter profiles" list={list} detail={detail} />;
}
