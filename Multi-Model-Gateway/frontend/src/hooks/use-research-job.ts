import { useEffect, useRef, useState } from "react";
import { researchApi } from "@/lib/api-endpoints";
import { useResearchStore } from "@/stores/research-store";
import type { ResearchJobDetail, ResearchEvent } from "@/lib/types";

export interface LiveProgress {
  stage: string;
  progress: number;
  message: string;
}

/**
 * Loads a research job's full detail (snapshot via GET), then — if it's still
 * running — subscribes to its SSE stream for live progress and the final
 * result. Also feeds status/progress back into the jobs-list store.
 */
export function useResearchJob(jobId: string | null) {
  const [detail, setDetail] = useState<ResearchJobDetail | null>(null);
  const [progress, setProgress] = useState<LiveProgress | null>(null);
  const [streaming, setStreaming] = useState(false);
  const patchJob = useResearchStore((s) => s.patchJob);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    abortRef.current?.abort();
    setDetail(null);
    setProgress(null);
    setStreaming(false);
    if (!jobId) return;

    let cancelled = false;
    const controller = new AbortController();
    abortRef.current = controller;

    (async () => {
      let job: ResearchJobDetail;
      try {
        job = await researchApi.get(jobId);
      } catch {
        return;
      }
      if (cancelled) return;
      setDetail(job);
      if (job.status !== "running" && job.status !== "queued") return;

      setStreaming(true);
      try {
        for await (const event of researchApi.stream(jobId, controller.signal)) {
          if (cancelled) break;
          const ev = event as ResearchEvent;
          if (ev.type === "progress") {
            setProgress({ stage: ev.stage, progress: ev.progress, message: ev.message });
            patchJob(jobId, { stage: ev.stage, progress: ev.progress, status: "running" });
          } else if (ev.type === "done") {
            if (ev.status === "complete") {
              setDetail((d) =>
                d ? { ...d, status: "complete", result: ev.result, sources: ev.sources, progress: 1 } : d
              );
              patchJob(jobId, { status: "complete", progress: 1 });
            } else {
              setDetail((d) => (d ? { ...d, status: "cancelled" } : d));
              patchJob(jobId, { status: "cancelled" });
            }
          } else if (ev.type === "error") {
            setDetail((d) => (d ? { ...d, status: "error", error: ev.message } : d));
            patchJob(jobId, { status: "error" });
          }
        }
      } catch {
        /* stream closed or aborted */
      } finally {
        if (!cancelled) setStreaming(false);
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [jobId, patchJob]);

  return { detail, progress, streaming };
}
