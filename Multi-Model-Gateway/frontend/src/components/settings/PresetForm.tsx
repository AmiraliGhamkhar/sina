import { useState } from "react";
import { Trash2 } from "lucide-react";
import { usePresetStore } from "@/stores/preset-store";
import { toast } from "@/stores/ui-store";
import { ApiError } from "@/lib/api-client";
import type { Preset, PresetCreate } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { Slider } from "@/components/ui/Slider";
import { StopStrings } from "@/components/ui/StopStrings";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

interface FormState {
  name: string;
  system_prompt: string;
  temperature: number;
  top_p: number;
  top_k: number;
  min_p: number;
  repeat_penalty: number;
  token_limit: string; // empty = unlimited
  stop_strings: string[];
}

const NEW_FORM: FormState = {
  name: "",
  system_prompt: "",
  temperature: 0.8,
  top_p: 0.95,
  top_k: 40,
  min_p: 0.05,
  repeat_penalty: 1.1,
  token_limit: "",
  stop_strings: [],
};

function presetToForm(p: Preset): FormState {
  return {
    name: p.name,
    system_prompt: p.system_prompt ?? "",
    temperature: p.temperature ?? 0.8,
    top_p: p.top_p ?? 0.95,
    top_k: p.top_k ?? 40,
    min_p: p.min_p ?? 0.05,
    repeat_penalty: p.repeat_penalty ?? 1.1,
    token_limit: p.token_limit != null ? String(p.token_limit) : "",
    stop_strings: p.stop_strings ?? [],
  };
}

interface PresetFormProps {
  /** The preset to edit, or null to create a new one. */
  preset: Preset | null;
  onSaved?: (preset: Preset) => void;
  onDeleted?: () => void;
  onCancel?: () => void;
  /** Label for the cancel/back button (defaults to "Cancel"). */
  cancelLabel?: string;
}

/**
 * Preset editor form — fields + save/delete/cancel. State is seeded from
 * `preset` on mount, so wrap in a `key` (e.g. preset id) to reset between
 * selections. Shared by the Presets page and the chat right-sidebar panel.
 */
export function PresetForm({ preset, onSaved, onDeleted, onCancel, cancelLabel }: PresetFormProps) {
  const createPreset = usePresetStore((s) => s.createPreset);
  const updatePreset = usePresetStore((s) => s.updatePreset);
  const deletePreset = usePresetStore((s) => s.deletePreset);

  const creating = preset === null;
  const [form, setForm] = useState<FormState>(() => (preset ? presetToForm(preset) : NEW_FORM));
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const buildPayload = (): PresetCreate => ({
    name: form.name.trim(),
    system_prompt: form.system_prompt.trim() || null,
    temperature: form.temperature,
    top_p: form.top_p,
    top_k: form.top_k,
    min_p: form.min_p,
    repeat_penalty: form.repeat_penalty,
    token_limit: form.token_limit.trim() ? Number(form.token_limit) : null,
    stop_strings: form.stop_strings.length ? form.stop_strings : null,
  });

  const save = async () => {
    if (!form.name.trim()) {
      toast.error("Preset name is required.");
      return;
    }
    setSaving(true);
    try {
      const saved = creating
        ? await createPreset(buildPayload())
        : await updatePreset(preset.id, buildPayload());
      toast.success(creating ? "Preset created." : "Preset saved.");
      onSaved?.(saved);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not save preset.");
    } finally {
      setSaving(false);
    }
  };

  const doDelete = async () => {
    if (!preset) return;
    setDeleting(true);
    try {
      await deletePreset(preset.id);
      toast.success("Preset deleted.");
      onDeleted?.();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not delete preset.");
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  return (
    <div className="flex flex-col gap-5 animate-fade-in">
      <Input
        label="Preset Name"
        value={form.name}
        onChange={(e) => set("name", e.target.value)}
        placeholder="e.g. Creative Writing"
      />

      <Textarea
        label="System Prompt"
        mono
        value={form.system_prompt}
        onChange={(e) => set("system_prompt", e.target.value)}
        placeholder="Instructions that set the AI's behavior…"
        className="min-h-[120px]"
      />

      <Slider
        label="Temperature"
        value={form.temperature}
        min={0}
        max={1}
        step={0.1}
        inputMax={Infinity}
        onChange={(v) => set("temperature", v)}
        endLabels={["Precise (0.0)", "Creative (1.0)"]}
      />

      <Slider
        label="Top P"
        value={form.top_p}
        min={0}
        max={1}
        step={0.01}
        onChange={(v) => set("top_p", v)}
      />

      <Input
        label="Top K"
        type="number"
        min={1}
        max={100}
        value={form.top_k}
        onChange={(e) => set("top_k", Number(e.target.value))}
        hint="Limit sampling to the top K tokens"
      />

      <Slider
        label="Min P"
        value={form.min_p}
        min={0}
        max={1}
        step={0.01}
        onChange={(v) => set("min_p", v)}
        hint="Minimum probability threshold"
      />

      <Slider
        label="Repeat Penalty"
        value={form.repeat_penalty}
        min={0}
        max={2}
        step={0.01}
        inputMax={Infinity}
        onChange={(v) => set("repeat_penalty", v)}
        hint="Higher values reduce repetition"
      />

      <Input
        label="Max Tokens"
        type="number"
        min={1}
        value={form.token_limit}
        onChange={(e) => set("token_limit", e.target.value)}
        placeholder="∞"
        hint="Leave empty for unlimited"
      />

      <StopStrings value={form.stop_strings} onChange={(v) => set("stop_strings", v)} />

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
        <Button variant="primary" onClick={save} isLoading={saving}>
          {creating ? "Create Preset" : "Save"}
        </Button>
        {!creating && (
          <Button
            variant="danger"
            leftIcon={<Trash2 size={15} />}
            onClick={() => setConfirmDelete(true)}
          >
            Delete
          </Button>
        )}
        {onCancel && (
          <Button variant="ghost" onClick={onCancel}>
            {cancelLabel ?? "Cancel"}
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete preset"
        message="Are you sure? This cannot be undone."
        confirmLabel="Delete Preset"
        isLoading={deleting}
        onConfirm={doDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
