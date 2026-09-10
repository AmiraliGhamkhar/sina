import { useState } from "react";
import { Trash2, Wand2 } from "lucide-react";
import { useTemplateStore } from "@/stores/template-store";
import { templateApi } from "@/lib/api-endpoints";
import { toast } from "@/stores/ui-store";
import { ApiError } from "@/lib/api-client";
import type { PromptTemplate } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { Spinner } from "@/components/ui/Spinner";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

interface FormState {
  name: string;
  description: string;
  structure: string;
}

export const DEFAULT_STRUCTURE = `quality,
art style, artist references,
camera,
subject,
accessories and clothes,
pose,
environment,
lighting`;

const NEW_FORM: FormState = { name: "", description: "", structure: DEFAULT_STRUCTURE };

function templateToForm(t: PromptTemplate): FormState {
  return { name: t.name, description: t.description ?? "", structure: t.structure };
}

interface TemplateFormProps {
  /** The template to edit, or null to create a new one. */
  template: PromptTemplate | null;
  onSaved?: (template: PromptTemplate) => void;
  onDeleted?: () => void;
  onCancel?: () => void;
  /** Label for the cancel/back button (defaults to "Cancel"). */
  cancelLabel?: string;
}

/**
 * Template editor form — fields, a test-rewrite tool, and save/delete/cancel.
 * State is seeded from `template` on mount, so wrap in a `key` (e.g. template
 * id) to reset between selections. Shared by the Templates page and the image
 * right-sidebar panel.
 */
export function TemplateForm({
  template,
  onSaved,
  onDeleted,
  onCancel,
  cancelLabel,
}: TemplateFormProps) {
  const createTemplate = useTemplateStore((s) => s.createTemplate);
  const updateTemplate = useTemplateStore((s) => s.updateTemplate);
  const deleteTemplate = useTemplateStore((s) => s.deleteTemplate);

  const creating = template === null;
  const [form, setForm] = useState<FormState>(() =>
    template ? templateToForm(template) : NEW_FORM
  );
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Test rewrite
  const [testPrompt, setTestPrompt] = useState("a cat wearing a hat");
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const save = async () => {
    if (!form.name.trim()) {
      toast.error("Template name is required.");
      return;
    }
    if (!form.structure.trim()) {
      toast.error("Structure is required.");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        description: form.description.trim(),
        structure: form.structure,
      };
      const saved = creating
        ? await createTemplate(payload)
        : await updateTemplate(template.id, payload);
      toast.success(creating ? "Template created." : "Template saved.");
      onSaved?.(saved);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not save template.");
    } finally {
      setSaving(false);
    }
  };

  const doDelete = async () => {
    if (!template) return;
    setDeleting(true);
    try {
      await deleteTemplate(template.id);
      toast.success("Template deleted.");
      onDeleted?.();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not delete template.");
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  const runTest = async () => {
    if (!testPrompt.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      // Saved template → use its id; unsaved/new → fall back to default structure.
      const { rewritten_prompt } = await templateApi.rewrite(
        testPrompt.trim(),
        template ? template.id : null
      );
      setTestResult(rewritten_prompt);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Rewrite failed.");
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="flex flex-col gap-5 animate-fade-in">
      <Input
        label="Template Name"
        value={form.name}
        onChange={(e) => set("name", e.target.value)}
        placeholder="e.g. Portrait Template"
      />
      <Input
        label="Description"
        value={form.description}
        onChange={(e) => set("description", e.target.value)}
        placeholder="What is this template for?"
      />
      <Textarea
        label="Structure (one category per line)"
        mono
        value={form.structure}
        onChange={(e) => set("structure", e.target.value)}
        className="min-h-[200px]"
        hint="Each line is a category the AI fills in, passed verbatim to the rewriter."
      />

      {/* Test rewrite */}
      <div className="flex flex-col gap-3 rounded-xl border border-border bg-bg-secondary/60 p-4">
        <div className="flex items-center gap-2">
          <Wand2 size={16} className="text-accent-secondary" />
          <h3 className="font-sans text-sm font-medium text-text-primary">Test Rewrite</h3>
        </div>
        <Textarea
          value={testPrompt}
          onChange={(e) => setTestPrompt(e.target.value)}
          placeholder="Enter a test prompt…"
          className="min-h-[60px]"
        />
        <div>
          <Button variant="secondary" onClick={runTest} isLoading={testing} leftIcon={<Wand2 size={15} />}>
            {testing ? "Rewriting…" : "Test Rewrite"}
          </Button>
          {creating && (
            <p className="mt-1.5 text-[0.75rem] text-text-muted">
              Save the template first to test its structure — until then the default structure is used.
            </p>
          )}
        </div>
        {testing && (
          <div className="flex items-center gap-2 text-[0.8125rem] text-text-muted">
            <Spinner size={14} /> The rewriter model loads on demand (~10–20s)…
          </div>
        )}
        {testResult && (
          <div className="rounded-lg border border-border bg-bg-primary p-3">
            <p className="font-mono text-[0.8125rem] leading-relaxed text-text-primary">
              {testResult}
            </p>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
        <Button variant="primary" onClick={save} isLoading={saving}>
          {creating ? "Create Template" : "Save"}
        </Button>
        {!creating && (
          <Button variant="danger" leftIcon={<Trash2 size={15} />} onClick={() => setConfirmDelete(true)}>
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
        title="Delete template"
        message="Are you sure? This cannot be undone."
        confirmLabel="Delete Template"
        isLoading={deleting}
        onConfirm={doDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
