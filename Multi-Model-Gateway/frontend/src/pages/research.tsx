import { useEffect, useState } from "react";
import { Telescope, AlertTriangle, X, ExternalLink } from "lucide-react";
import { useResearchStore } from "@/stores/research-store";
import { useResearchJob } from "@/hooks/use-research-job";
import { toast } from "@/stores/ui-store";
import { ApiError } from "@/lib/api-client";
import type { ResearchJob, ResearchStatus } from "@/lib/types";
import { cn, truncate, formatRelativeTime } from "@/lib/utils";
import { TwoPanel } from "@/components/layout/TwoPanel";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { Markdown } from "@/components/chat/Markdown";

const STATUS_STYLE: Record<ResearchStatus, string> = {
  queued: "bg-bg-elevated/60 text-text-secondary",
  running: "bg-accent-secondary/15 text-accent-secondary",
  complete: "bg-success/15 text-success",
  cancelled: "bg-bg-elevated/60 text-text-muted",
  error: "bg-danger/15 text-danger",
  failed: "bg-danger/15 text-danger",
};

function StatusChip({ status }: { status: ResearchStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[0.7rem] font-medium capitalize",
        STATUS_STYLE[status]
      )}
    >
      {status === "running" && <Spinner size={10} />}
      {status}
    </span>
  );
}

function JobDetail({ jobId, onCancel }: { jobId: string; onCancel: () => void }) {
  const { detail, progress, streaming } = useResearchJob(jobId);

  if (!detail) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  const running = detail.status === "running" || detail.status === "queued";
  const pct = Math.round((progress?.progress ?? detail.progress ?? 0) * 100);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 animate-fade-in">
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-xl text-text-primary">{detail.query}</h2>
        <StatusChip status={detail.status} />
      </div>

      {running && (
        <div className="flex flex-col gap-2 rounded-xl border border-border bg-bg-secondary/60 p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="flex items-center gap-2 text-sm text-text-secondary">
              {streaming && <Spinner size={14} />}
              {progress?.message || progress?.stage || detail.stage || "Starting…"}
            </span>
            <Button variant="secondary" size="sm" leftIcon={<X size={14} />} onClick={onCancel}>
              Cancel
            </Button>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-tertiary">
            <div
              className="h-full rounded-full bg-accent-primary transition-[width] duration-300"
              style={{ width: `${Math.max(pct, 4)}%` }}
            />
          </div>
          <span className="text-[0.75rem] text-text-muted">{pct}%</span>
        </div>
      )}

      {(detail.status === "error" || detail.status === "failed") && (
        <div className="flex items-center gap-2 rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">
          <AlertTriangle size={16} />
          {detail.error || "Research failed."}
        </div>
      )}

      {detail.status === "cancelled" && !detail.result && (
        <p className="text-sm text-text-muted">This research job was cancelled.</p>
      )}

      {detail.result && (
        <div className="rounded-xl border border-border bg-bg-secondary/40 p-5">
          <Markdown content={detail.result} />
        </div>
      )}

      {detail.sources && detail.sources.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="font-sans text-sm font-medium text-text-secondary">
            Sources ({detail.sources.length})
          </h3>
          <ol className="flex flex-col gap-1.5">
            {detail.sources.map((s) => (
              <li key={s.n} className="flex items-baseline gap-2 text-[0.8125rem]">
                <span className="shrink-0 font-mono text-text-muted">[{s.n}]</span>
                <a
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group flex min-w-0 items-center gap-1 text-accent-secondary hover:underline"
                  title={s.url}
                >
                  <span className="truncate">{s.title || s.url}</span>
                  <ExternalLink size={12} className="shrink-0 opacity-60" />
                </a>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

export default function ResearchPage() {
  const { jobs, isLoading, hasLoaded, fetchJobs, createJob, cancelJob } = useResearchStore();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!hasLoaded) void fetchJobs();
  }, [hasLoaded, fetchJobs]);

  const submit = async () => {
    const q = query.trim();
    if (!q || submitting) return;
    setSubmitting(true);
    try {
      const id = await createJob(q);
      setQuery("");
      setSelectedId(id);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not start research.");
    } finally {
      setSubmitting(false);
    }
  };

  const doCancel = async () => {
    if (!selectedId) return;
    try {
      await cancelJob(selectedId);
    } catch {
      toast.error("Could not cancel.");
    }
  };

  const list = (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        <Textarea
          label="New research"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask a question to research in depth…"
          className="min-h-[80px]"
        />
        <Button
          variant="primary"
          fullWidth
          onClick={submit}
          isLoading={submitting}
          leftIcon={<Telescope size={16} />}
        >
          Research
        </Button>
      </div>

      <div className="border-t border-border pt-1">
        {isLoading && jobs.length === 0 ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : jobs.length === 0 ? (
          <p className="px-2 py-6 text-center text-sm text-text-muted">No research yet.</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {jobs.map((j: ResearchJob) => (
              <button
                key={j.id}
                onClick={() => setSelectedId(j.id)}
                className={cn(
                  "flex flex-col gap-1 rounded-lg border-l-2 px-3 py-2.5 text-left transition-colors",
                  j.id === selectedId
                    ? "border-accent-primary bg-bg-tertiary"
                    : "border-transparent hover:bg-bg-tertiary/60"
                )}
              >
                <span className="truncate text-sm font-medium text-text-primary">
                  {truncate(j.query, 60)}
                </span>
                <span className="flex items-center gap-2">
                  <StatusChip status={j.status} />
                  <span className="text-[0.7rem] text-text-muted">
                    {formatRelativeTime(j.created_at)}
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );

  const detail = selectedId ? (
    <JobDetail jobId={selectedId} onCancel={doCancel} />
  ) : (
    <EmptyState
      icon={<Telescope size={40} strokeWidth={1.5} />}
      title="Deep research"
      description="Ask a question and the agent will search, read, and synthesize a sourced answer. Past runs appear on the left."
    />
  );

  return (
    <TwoPanel title="Deep Research" subtitle="Multi-step web research with sources" list={list} detail={detail} />
  );
}
