import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Cpu, RotateCw } from "lucide-react";
import { hfApi, hardwareApi } from "@/lib/api-endpoints";
import type { HardwareInfo, HfCookbookResponse, HfModelDetail } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ModelList } from "@/components/models/ModelList";
import { ModelDetail } from "@/components/models/ModelDetail";
import { LocalModels } from "@/components/models/LocalModels";

const DEFAULT_CONTEXT = 8192;

type Tab = "local" | "cloud";

function gb(mb: number): string {
  return `${(mb / 1024).toFixed(1)} GB`;
}

export default function ModelsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedRepo = searchParams.get("repo");

  // Top-level tabs — the Models window unifies the old Cookbook page (Local:
  // installed models + fit check) and the HF model browser (Cloud) under one
  // page with a shared hardware chip in the tab bar.
  const [tab, setTab] = useState<Tab>("local");
  const [refreshKey, setRefreshKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  // Hardware chip (top tab bar) — fetched once on mount and again on Refresh.
  const [hardware, setHardware] = useState<HardwareInfo | null>(null);
  const [hardwareLoading, setHardwareLoading] = useState(true);

  // Cloud tab — left pane (catalog list)
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(10);
  const [contextTokens, setContextTokens] = useState(DEFAULT_CONTEXT);
  const [listData, setListData] = useState<HfCookbookResponse | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listPending, setListPending] = useState(false);
  const [listError, setListError] = useState(false);

  // Cloud tab — right pane (model detail)
  const [detail, setDetail] = useState<HfModelDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [detailError, setDetailError] = useState(false);

  // Monotonic request ids — a stale response (older search/repo) is dropped.
  const listSeq = useRef(0);
  const detailSeq = useRef(0);
  // URL-driven repo changes clear the detail during render (React's
  // adjust-state-when-props-change pattern) so the previous repo's detail
  // never paints under the new selection; context-token refetches keep it.
  const [prevRepo, setPrevRepo] = useState(selectedRepo);
  if (prevRepo !== selectedRepo) {
    setPrevRepo(selectedRepo);
    setDetail(null);
    setDetailLoading(true);
    setDetailError(false);
  }

  const loadList = useCallback((q: string, lim: number, tokens: number) => {
    const seq = ++listSeq.current;
    return hfApi
      .models(q, lim, tokens)
      .then((res) => {
        if (seq !== listSeq.current) return;
        setListData(res);
        setListError(false);
      })
      .catch(() => {
        if (seq === listSeq.current) setListError(true);
      })
      .finally(() => {
        if (seq === listSeq.current) {
          setListLoading(false);
          setListPending(false);
        }
      });
  }, []);

  const loadDetail = useCallback((repoId: string, tokens: number): Promise<void> => {
    if (!repoId) return Promise.resolve();
    const seq = ++detailSeq.current;
    return hfApi
      .detail(repoId, tokens)
      .then((res) => {
        if (seq !== detailSeq.current) return;
        setDetail(res);
        setDetailError(false);
      })
      .catch(() => {
        if (seq === detailSeq.current) {
          // The latest request failed — drop any stale detail so the error
          // state renders instead of silently showing out-of-date data.
          setDetail(null);
          setDetailError(true);
        }
      })
      .finally(() => {
        if (seq === detailSeq.current) setDetailLoading(false);
      });
  }, []);

  // setState only ever runs in promise callbacks — the mount effect below
  // never sets state synchronously (react-hooks/set-state-in-effect safe).
  const loadHardware = useCallback(() => {
    return hardwareApi
      .hardware()
      .then((res) => setHardware(res))
      .catch(() => {
        // A failed probe keeps whatever was last detected; the strip only
        // reads as "No GPU detected" when nothing has ever loaded.
      })
      .finally(() => setHardwareLoading(false));
  }, []);

  useEffect(() => {
    void loadList(query, limit, contextTokens);
  }, [query, limit, contextTokens, loadList]);

  useEffect(() => {
    void loadDetail(selectedRepo ?? "", contextTokens);
  }, [selectedRepo, contextTokens, loadDetail]);

  useEffect(() => {
    void loadHardware();
  }, [loadHardware]);

  const handleSearch = () => {
    const q = search.trim();
    setListError(false);
    setListLoading(true);
    setListPending(true);
    if (q === query) {
      // Same query — the list effect won't re-fire, so fetch directly.
      void loadList(q, limit, contextTokens);
    } else {
      setQuery(q);
    }
  };

  const handleRetryList = () => {
    setListError(false);
    setListLoading(true);
    setListPending(true);
    void loadList(query, limit, contextTokens);
  };

  const handleRetryDetail = () => {
    setDetailLoading(true);
    setDetailError(false);
    void loadDetail(selectedRepo ?? "", contextTokens);
  };

  const handleContextChange = (tokens: number) => {
    // Keep the current detail visible but flip loading on — the pane shows
    // the refreshing indicator instead of presenting stale fit data as fresh.
    setContextTokens(tokens);
    setDetailLoading(true);
    setDetailError(false);
  };

  const handleSelect = (repoId: string) => {
    // Clear immediately so the old repo's detail never paints under the new
    // selection; the effect refetches once the URL lands.
    setDetail(null);
    setDetailLoading(true);
    setDetailError(false);
    if (repoId === selectedRepo) {
      // Same repo — the URL won't change, so the detail effect won't re-fire.
      void loadDetail(repoId, contextTokens);
    } else {
      setSearchParams({ repo: repoId });
    }
  };

  // Refresh refetches the active tab + the hardware chip.
  const refresh = () => {
    setRefreshing(true);
    setHardwareLoading(true);
    const jobs: Array<Promise<unknown>> = [loadHardware()];
    if (tab === "local") {
      // LocalModels refetches itself when refreshKey increments.
      setRefreshKey((k) => k + 1);
    } else {
      setListLoading(true);
      setListPending(true);
      jobs.push(loadList(query, limit, contextTokens));
      if (selectedRepo) {
        setDetailLoading(true);
        setDetailError(false);
        jobs.push(loadDetail(selectedRepo, contextTokens));
      }
    }
    void Promise.allSettled(jobs).then(() => setRefreshing(false));
  };

  // Primary GPU for the chip — the card with the most total VRAM (mirrors the
  // backend's single-GPU fit assumption of max total VRAM).
  const primaryGpu = hardware?.gpus?.length
    ? hardware.gpus.reduce((a, b) => (b.vram_total_mb > a.vram_total_mb ? b : a))
    : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-border px-6 py-4 max-md:pl-14">
        <div>
          <h1 className="text-2xl text-text-primary">Models</h1>
          <p className="text-sm text-text-secondary">
            Installed models and the Hugging Face catalog — fit-checked against your hardware
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<RotateCw size={14} />}
          onClick={refresh}
          isLoading={refreshing}
        >
          Refresh
        </Button>
      </header>

      {/* Tab control — hardware chip on the left, Local | Cloud tabs on the right */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-6 py-3">
        {/* Detected hardware chip — name · total VRAM · total RAM (no free-VRAM
            metric). Shared across both tabs. */}
        <div className="flex items-center">
          {hardwareLoading && !hardware ? (
            <Skeleton className="h-5 w-40" />
          ) : !hardware?.gpu_available || !primaryGpu ? (
            <span className="flex items-center gap-1.5 text-[0.8125rem] text-text-muted">
              <Cpu size={13} className="text-accent-primary" />
              No GPU detected
            </span>
          ) : (
            <div className="flex items-center gap-2 rounded-lg border border-border bg-bg-tertiary px-3 py-1.5">
              <Cpu size={13} className="text-accent-primary" />
              <span
                className="max-w-[200px] truncate text-[0.8125rem] font-medium text-text-primary"
                title={primaryGpu.name}
              >
                {primaryGpu.name}
              </span>
              <span className="whitespace-nowrap font-mono text-[0.75rem] text-text-secondary">
                {gb(primaryGpu.vram_total_mb)} VRAM
                {hardware.ram_total_mb != null ? ` + ${gb(hardware.ram_total_mb)} RAM` : ""}
              </span>
            </div>
          )}
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          {(
            [
              { id: "local", label: "Local" },
              { id: "cloud", label: "Cloud" },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                "rounded-lg border px-3 py-1.5 text-[0.8125rem] transition-colors",
                tab === t.id
                  ? "border-accent-primary bg-accent-primary text-white"
                  : "border-border bg-bg-tertiary text-text-secondary hover:text-text-primary"
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Scrollable body — renders the active tab. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "cloud" ? (
          <div className="grid h-full min-h-0 grid-cols-[minmax(320px,380px)_1fr] max-lg:grid-cols-1 max-lg:overflow-y-auto">
            <div className="min-h-0 overflow-y-auto border-r border-border max-lg:overflow-visible max-lg:border-r-0 max-lg:border-b">
              <ModelList
                models={listData?.models ?? []}
                loading={listLoading}
                pending={listPending}
                error={listError}
                search={search}
                onSearchChange={setSearch}
                limit={limit}
                onLimitChange={setLimit}
                onSubmit={handleSearch}
                onRetry={handleRetryList}
                selectedRepo={selectedRepo}
                onSelect={handleSelect}
              />
            </div>
            <div className="min-h-0 overflow-y-auto max-lg:overflow-visible">
              <ModelDetail
                repoId={selectedRepo}
                detail={detail}
                loading={detailLoading}
                error={detailError}
                contextTokens={contextTokens}
                onContextChange={handleContextChange}
                onRetry={handleRetryDetail}
              />
            </div>
          </div>
        ) : (
          <LocalModels refreshKey={refreshKey} />
        )}
      </div>
    </div>
  );
}
