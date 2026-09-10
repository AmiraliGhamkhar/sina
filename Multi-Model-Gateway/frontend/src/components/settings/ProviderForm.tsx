import { useState } from "react";
import { PlugZap, Trash2 } from "lucide-react";
import { useProviderStore } from "@/stores/provider-store";
import { providerApi } from "@/lib/api-endpoints";
import { toast } from "@/stores/ui-store";
import { ApiError } from "@/lib/api-client";
import type {
  ProviderCreate,
  ProviderRole,
  ProviderRow,
  ProviderTestResult,
  ProviderType,
} from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input, PasswordInput } from "@/components/ui/Input";
import { Dropdown } from "@/components/ui/Dropdown";
import type { DropdownOption } from "@/components/ui/Dropdown";
import { Toggle } from "@/components/ui/Toggle";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

const TYPE_OPTIONS: DropdownOption[] = [
  { value: "openai_compatible", label: "OpenAI-compatible" },
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "google", label: "Google" },
  { value: "openrouter", label: "OpenRouter" },
];

const ROLE_OPTIONS: DropdownOption[] = [
  { value: "local", label: "Local" },
  { value: "cloud", label: "Cloud" },
];

interface FormState {
  name: string;
  type: ProviderType;
  role: ProviderRole;
  base_url: string;
  api_key: string; // empty = none on create / unchanged on edit
  default_model: string;
  is_default: boolean;
  enabled: boolean;
}

const NEW_FORM: FormState = {
  name: "",
  type: "openai_compatible",
  role: "local",
  base_url: "",
  api_key: "",
  default_model: "",
  is_default: false,
  enabled: true,
};

function providerToForm(p: ProviderRow): FormState {
  return {
    name: p.name,
    type: p.type,
    role: p.role,
    base_url: p.base_url ?? "",
    api_key: "",
    default_model: p.default_model ?? "",
    is_default: p.is_default,
    enabled: p.enabled,
  };
}

interface ProviderFormProps {
  /** The provider to edit, or null to create a new one. */
  provider: ProviderRow | null;
  onSaved?: (provider: ProviderRow) => void;
  onDeleted?: () => void;
  onCancel?: () => void;
  /** Label for the cancel/back button (defaults to "Cancel"). */
  cancelLabel?: string;
}

/**
 * Provider editor form — fields + save/delete/cancel + a live connection test.
 * State is seeded from `provider` on mount, so wrap in a `key` (e.g. provider
 * id) to reset between selections.
 */
export function ProviderForm({
  provider,
  onSaved,
  onDeleted,
  onCancel,
  cancelLabel,
}: ProviderFormProps) {
  const createProvider = useProviderStore((s) => s.createProvider);
  const updateProvider = useProviderStore((s) => s.updateProvider);
  const deleteProvider = useProviderStore((s) => s.deleteProvider);

  const creating = provider === null;
  const [form, setForm] = useState<FormState>(() => (provider ? providerToForm(provider) : NEW_FORM));
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const buildPayload = (): ProviderCreate => {
    const payload: ProviderCreate = {
      name: form.name.trim(),
      type: form.type,
      role: form.role,
      base_url: form.base_url.trim() || null,
      default_model: form.default_model.trim() || null,
      is_default: form.is_default,
      enabled: form.enabled,
    };
    // Keys are write-only: blank means "no key" on create and "leave the
    // existing key untouched" on edit, so only send one when provided.
    if (form.api_key.trim()) payload.api_key = form.api_key.trim();
    return payload;
  };

  const save = async () => {
    if (!form.name.trim()) {
      toast.error("Provider name is required.");
      return;
    }
    if (form.type === "openai_compatible" && !form.base_url.trim()) {
      toast.error("base_url is required for openai-compatible providers.");
      return;
    }
    setSaving(true);
    try {
      const saved = creating
        ? await createProvider(buildPayload())
        : await updateProvider(provider.id, buildPayload());
      toast.success(creating ? "Provider created." : "Provider saved.");
      onSaved?.(saved);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not save provider.");
    } finally {
      setSaving(false);
    }
  };

  const doDelete = async () => {
    if (!provider) return;
    setDeleting(true);
    try {
      await deleteProvider(provider.id);
      toast.success("Provider deleted.");
      onDeleted?.();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not delete provider.");
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  const runTest = async () => {
    if (!provider) return;
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await providerApi.test(provider.id));
    } catch (err) {
      setTestResult({
        ok: false,
        error: err instanceof ApiError ? err.detail : "Test failed.",
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="flex flex-col gap-5 animate-fade-in">
      <Input
        label="Provider Name"
        value={form.name}
        onChange={(e) => set("name", e.target.value)}
        placeholder="e.g. Local (LM Studio)"
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label className="text-[0.8125rem] font-medium text-text-secondary">Type</label>
          <Dropdown
            value={form.type}
            options={TYPE_OPTIONS}
            onChange={(v) => set("type", v as ProviderType)}
            className="w-full"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-[0.8125rem] font-medium text-text-secondary">Role</label>
          <Dropdown
            value={form.role}
            options={ROLE_OPTIONS}
            onChange={(v) => set("role", v as ProviderRole)}
            className="w-full"
          />
        </div>
      </div>

      <div className="flex flex-col gap-5 border-t border-border pt-5">
        <Input
          label="Base URL"
          value={form.base_url}
          onChange={(e) => set("base_url", e.target.value)}
          placeholder="http://localhost:1234/v1"
          hint={
            form.type === "openai_compatible"
              ? "Required for OpenAI-compatible providers"
              : undefined
          }
        />

        <div className="flex flex-col gap-1">
          <PasswordInput
            label="API Key"
            value={form.api_key}
            onChange={(e) => set("api_key", e.target.value)}
            placeholder={creating ? "sk-…" : "••••••••"}
            hint={
              creating
                ? "Optional (local servers may not need one)"
                : "Leave blank to keep current key"
            }
          />
          {!creating && provider.api_key_masked && (
            <span className="text-[0.8125rem] text-text-muted">
              Current key:{" "}
              <span className="font-mono text-text-secondary">{provider.api_key_masked}</span>
            </span>
          )}
        </div>

        <Input
          label="Default Model"
          value={form.default_model}
          onChange={(e) => set("default_model", e.target.value)}
          placeholder="e.g. llama-3.2-3b-instruct"
          hint="Used when a request doesn't pin a model"
        />
      </div>

      <div className="flex flex-col gap-4 border-t border-border pt-5">
        <Toggle
          checked={form.is_default}
          onChange={(v) => set("is_default", v)}
          label="Default provider"
          description="Preferred for this role (local or cloud) when routing a request"
        />
        <Toggle
          checked={form.enabled}
          onChange={(v) => set("enabled", v)}
          label="Enabled"
          description="Uncheck to pause this provider without deleting it"
        />
      </div>

      {!creating && (
        <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
          <Button
            variant="secondary"
            leftIcon={<PlugZap size={15} />}
            onClick={runTest}
            isLoading={testing}
            disabled={saving}
          >
            Test connection
          </Button>
          {testResult &&
            (testResult.ok ? (
              <span className="text-sm text-success">
                OK — {testResult.model ?? "connected"}
              </span>
            ) : (
              <span className="text-sm text-danger">
                {testResult.error ?? "Test failed."}
              </span>
            ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
        <Button variant="primary" onClick={save} isLoading={saving}>
          {creating ? "Create Provider" : "Save"}
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
        title="Delete provider"
        message="Are you sure? This cannot be undone."
        confirmLabel="Delete Provider"
        isLoading={deleting}
        onConfirm={doDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
