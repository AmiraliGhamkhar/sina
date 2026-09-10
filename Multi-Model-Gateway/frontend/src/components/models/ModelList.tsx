import { RotateCw, Search } from "lucide-react";
import type { HfModelSummary } from "@/lib/types";
import { cn, formatCompact, formatRelativeTime } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";

const LIMIT_OPTIONS = [10, 25, 50];

interface ModelListProps {
  models: HfModelSummary[];
  loading: boolean;
  pending: boolean;
  error: boolean;
  search: string;
  onSearchChange: (value: string) => void;
  limit: number;
  onLimitChange: (value: number) => void;
  onSubmit: () => void;
  onRetry: () => void;
  selectedRepo: string | null;
  onSelect: (repoId: string) => void;
}

function sublabel(m: HfModelSummary): string {
  const parts: string[] = [];
  if (m.params_b != null) parts.push(`${m.params_b}B`);
  parts.push(`${formatCompact(m.downloads)} downloads`);
  parts.push(`${formatCompact(m.likes)} likes`);
  if (m.lastModified) parts.push(formatRelativeTime(m.lastModified));
  return parts.join(" · ");
}

export function ModelList({
  models,
  loading,
  pending,
  error,
  search,
  onSearchChange,
  limit,
  onLimitChange,
  onSubmit,
  onRetry,
  selectedRepo,
  onSelect,
}: ModelListProps) {
  return (
    <div className="flex flex-col gap-3 p-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
        className="flex flex-col gap-2"
      >
        <input
          type="text"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search Hugging Face models…"
          className="h-9 min-w-0 flex-1 rounded-lg border border-border bg-bg-tertiary px-3 text-[0.8125rem] text-text-primary outline-none transition-colors placeholder:text-text-muted focus:border-accent-primary"
        />
        <div className="flex items-center gap-2">
          <select
            value={limit}
            onChange={(e) => onLimitChange(Number(e.target.value))}
            className="h-9 rounded-lg border border-border bg-bg-tertiary px-3 text-[0.8125rem] text-text-secondary outline-none transition-colors focus:border-accent-primary"
          >
            {LIMIT_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n} results
              </option>
            ))}
          </select>
          <Button
            type="submit"
            size="sm"
            leftIcon={<Search size={14} />}
            isLoading={pending}
            className="flex-1"
          >
            Search
          </Button>
        </div>
      </form>

      {error && !loading ? (
        <div className="flex items-center justify-between gap-2 rounded-xl border border-border bg-bg-tertiary/50 px-4 py-3">
          <span className="text-sm text-text-muted">Couldn't load Hugging Face models.</span>
          <button
            onClick={onRetry}
            className="flex items-center gap-1 text-[0.8125rem] text-accent-primary hover:underline"
          >
            <RotateCw size={13} /> Retry
          </button>
        </div>
      ) : loading && models.length === 0 ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : models.length === 0 ? (
        <p className="text-sm text-text-muted">No models found — try a different search.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {models.map((m) => {
            const active = m.id === selectedRepo;
            return (
              <li key={m.id}>
                <button
                  type="button"
                  onClick={() => onSelect(m.id)}
                  className={cn(
                    "w-full rounded-lg border px-3 py-2.5 text-left transition-colors",
                    active
                      ? "border-accent-primary/60 bg-accent-primary/10"
                      : "border-transparent hover:border-border hover:bg-bg-tertiary/50"
                  )}
                >
                  <span className="block truncate font-mono text-[0.8125rem] text-text-primary">
                    {m.id}
                  </span>
                  <span className="block truncate text-[0.7rem] text-text-muted">{sublabel(m)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
