import { useCallback, useEffect, useRef, useState } from "react";
import { RotateCw } from "lucide-react";
import { hardwareApi } from "@/lib/api-endpoints";
import type { CookbookModel, CookbookResponse } from "@/lib/types";
import { LOCAL_VERDICT } from "@/lib/fit-verdict";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/Skeleton";

const CONTEXT_OPTIONS = [2048, 4096, 8192, 16384, 32768];
const DEFAULT_CONTEXT = 8192;

interface LocalModelsProps {
  /** Incremented by the page's Refresh button to trigger a refetch. */
  refreshKey: number;
}

/** Local tab of the Models window — installed LM Studio models fit-scored
 * against the detected hardware at a chosen context size (GET /v1/cookbook). */
export function LocalModels({ refreshKey }: LocalModelsProps) {
  const [contextTokens, setContextTokens] = useState(DEFAULT_CONTEXT);
  const [data, setData] = useState<CookbookResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // Monotonic request id — a stale response (older context/refresh) is dropped.
  const seqRef = useRef(0);

  // setState only ever runs in promise callbacks, so the effect below never
  // sets state synchronously (react-hooks/set-state-in-effect safe).
  const load = useCallback((tokens: number) => {
    const seq = ++seqRef.current;
    return hardwareApi
      .cookbook(tokens)
      .then((res) => {
        if (seq !== seqRef.current) return;
        setData(res);
        setError(false);
      })
      .catch(() => {
        if (seq === seqRef.current) setError(true);
      })
      .finally(() => {
        if (seq === seqRef.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    void load(contextTokens);
  }, [contextTokens, load, refreshKey]);

  // Event handlers may flip loading synchronously — the effect that follows a
  // context change refetches and clears it once the request settles.
  const handleContextChange = (tokens: number) => {
    setContextTokens(tokens);
    setLoading(true);
    setError(false);
  };

  const handleRetry = () => {
    setLoading(true);
    setError(false);
    void load(contextTokens);
  };

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5 p-6">
      {/* Context selector */}
      <div className="flex flex-col gap-1.5">
        <label className="text-sm font-medium text-text-secondary">Context window (tokens)</label>
        <div className="flex flex-wrap gap-1.5">
          {CONTEXT_OPTIONS.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => handleContextChange(n)}
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

      {/* Recommendation (local-only — the HF browser has no equivalent) */}
      {data?.recommendation && (
        <div className="flex items-start gap-2 rounded-xl border border-accent-primary/30 bg-accent-primary/10 px-4 py-3">
          <p className="text-sm leading-relaxed text-text-primary">{data.recommendation}</p>
        </div>
      )}

      {loading && data && (
        <p className="text-[0.75rem] text-text-muted">Refreshing fit checks…</p>
      )}

      {error ? (
        <div className="flex items-center justify-between gap-2 rounded-xl border border-border bg-bg-tertiary/50 px-4 py-3">
          <span className="text-sm text-text-muted">Couldn't load the cookbook.</span>
          <button
            onClick={handleRetry}
            className="flex items-center gap-1 text-[0.8125rem] text-accent-primary hover:underline"
          >
            <RotateCw size={13} /> Retry
          </button>
        </div>
      ) : loading && !data ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : !data || data.models.length === 0 ? (
        <p className="text-sm text-text-muted">
          No local models found — start LM Studio and load a model.
        </p>
      ) : (
        <LocalModelTable models={data.models} />
      )}
    </div>
  );
}

function LocalModelTable({ models }: { models: CookbookModel[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="bg-bg-tertiary/60 text-left text-[0.75rem] uppercase tracking-wide text-text-muted">
            <th className="px-3 py-2 font-medium">Model</th>
            <th className="px-3 py-2 font-medium">Quant</th>
            <th className="px-3 py-2 font-medium">Needs</th>
            <th className="px-3 py-2 font-medium">Verdict</th>
            <th className="px-3 py-2 font-medium">Notes</th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => {
            const v = LOCAL_VERDICT[m.verdict] ?? LOCAL_VERDICT.unknown;
            return (
              <tr key={m.id} className="border-t border-border align-top">
                <td className="px-3 py-2.5">
                  <span className="block font-mono text-[0.8125rem] text-text-primary">{m.id}</span>
                  <span className="text-[0.7rem] text-text-muted">
                    {m.params_b}B · {m.source}
                  </span>
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 font-mono text-[0.8125rem] text-text-secondary">
                  {m.quant}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 font-mono text-[0.8125rem] text-text-secondary">
                  {m.need_gb != null ? `${m.need_gb.toFixed(1)} GB` : "—"}
                </td>
                <td className="px-3 py-2.5">
                  <span
                    className={cn(
                      "inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-[0.7rem] font-medium",
                      v.cls
                    )}
                  >
                    {v.label}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-[0.8125rem] text-text-muted">{m.rationale}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
