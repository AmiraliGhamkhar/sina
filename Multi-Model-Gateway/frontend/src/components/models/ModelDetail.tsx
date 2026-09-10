import { ArrowDown, Copy, RotateCw, Star } from "lucide-react";
import type { HfModelDetail } from "@/lib/types";
import { cn, formatCompact, formatRelativeTime } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/stores/ui-store";
import { CapabilityBadge } from "@/components/models/CapabilityBadge";
import { QuantRow } from "@/components/models/QuantRow";

const CONTEXT_OPTIONS = [2048, 4096, 8192, 16384, 32768];

interface ModelDetailProps {
  repoId: string | null;
  detail: HfModelDetail | null;
  loading: boolean;
  error: boolean;
  contextTokens: number;
  onContextChange: (tokens: number) => void;
  onRetry: () => void;
}

const copyRepoId = async (repoId: string) => {
  try {
    await navigator.clipboard.writeText(repoId);
    toast.success("Copied model id");
  } catch {
    toast.error("Couldn't copy — clipboard unavailable");
  }
};

function MetaCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[0.7rem] uppercase tracking-wide text-text-muted">{label}</span>
      <div className="flex flex-wrap gap-1">
        {value && value !== "—" ? (
          <Badge className="max-w-full truncate">{value}</Badge>
        ) : (
          <span className="text-[0.8125rem] text-text-muted">—</span>
        )}
      </div>
    </div>
  );
}

export function ModelDetail({
  repoId,
  detail,
  loading,
  error,
  contextTokens,
  onContextChange,
  onRetry,
}: ModelDetailProps) {
  if (!repoId) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <p className="text-sm text-text-muted">Select a model to see details.</p>
      </div>
    );
  }

  if (error && !detail) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="flex flex-col items-center gap-3">
          <p className="text-sm text-text-muted">Couldn't load model details.</p>
          <Button size="sm" variant="secondary" leftIcon={<RotateCw size={14} />} onClick={onRetry}>
            Retry
          </Button>
        </div>
      </div>
    );
  }

  if (loading && !detail) {
    return (
      <div className="flex flex-col gap-4 p-6">
        <Skeleton className="h-7 w-2/3" />
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-10 w-full" />
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <p className="text-sm text-text-muted">Select a model to see details.</p>
      </div>
    );
  }

  const { downloads, likes, last_modified, description, params_b, arch, pipeline_tag, formats, capabilities, quants, has_gguf } =
    detail;

  return (
    <div className="flex flex-col gap-5 p-6">
      {loading && (
        <div className="flex items-center gap-2 text-[0.75rem] text-text-muted">
          <Spinner size={12} />
          Refreshing details…
        </div>
      )}

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate font-mono text-lg text-text-primary">{detail.repo_id}</h2>
            <button
              type="button"
              onClick={() => void copyRepoId(detail.repo_id)}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-muted transition-colors hover:bg-bg-tertiary hover:text-text-primary"
              title="Copy model id"
              aria-label="Copy model id"
            >
              <Copy size={15} />
            </button>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.8125rem] text-text-secondary">
            <span className="inline-flex items-center gap-1.5">
              <ArrowDown size={13} className="text-text-muted" />
              {formatCompact(downloads)} downloads
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Star size={13} className="text-accent-secondary" />
              {formatCompact(likes)} likes
            </span>
            {last_modified && (
              <span className="text-text-muted">Last updated: {formatRelativeTime(last_modified)}</span>
            )}
          </div>
        </div>
      </div>

      {description ? (
        <p className="text-sm leading-relaxed text-text-secondary">{description}</p>
      ) : null}

      {/* Metadata strip */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetaCell label="Params" value={params_b != null ? `${params_b}B` : "—"} />
        <MetaCell label="Arch" value={arch ?? "—"} />
        <MetaCell label="Domain" value={pipeline_tag ?? "—"} />
        <div className="flex flex-col gap-1">
          <span className="text-[0.7rem] uppercase tracking-wide text-text-muted">Format</span>
          <div className="flex flex-wrap gap-1">
            {formats.length > 0 ? (
              formats.map((f) => <Badge key={f}>{f}</Badge>)
            ) : (
              <span className="text-[0.8125rem] text-text-muted">—</span>
            )}
          </div>
        </div>
      </div>

      {/* Capabilities — render only when the backend scraped any */}
      {capabilities.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-medium text-text-secondary">Capabilities</h3>
          <div className="flex flex-wrap gap-1.5">
            {capabilities.map((c) => (
              <CapabilityBadge key={c} capability={c} />
            ))}
          </div>
        </section>
      )}

      {/* Download options */}
      <section className="flex flex-col gap-3">
        <h3 className="mb-2 text-sm font-medium text-text-secondary">Download Options</h3>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-text-secondary">Context window (tokens)</label>
          <div className="flex flex-wrap gap-1.5">
            {CONTEXT_OPTIONS.map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => onContextChange(n)}
                className={cn(
                  "rounded-lg border px-3 py-1.5 text-[0.8125rem] transition-colors",
                  contextTokens === n
                    ? "border-accent-primary bg-accent-primary text-white"
                    : "border-border bg-bg-tertiary text-text-secondary hover:text-text-primary"
                )}
              >
                {n.toLocaleString()}
              </button>
            ))}
          </div>
        </div>

        {!has_gguf && quants.length === 0 ? (
          <p className="text-sm text-text-muted">No GGUF quantization available for this model.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {quants.map((q) => (
              <QuantRow key={q.quant} quant={q} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
