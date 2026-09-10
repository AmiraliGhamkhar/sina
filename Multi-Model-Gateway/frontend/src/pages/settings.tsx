import { useCallback, useEffect, useState } from "react";
import {
  RefreshCw,
  Cpu,
  Cloud,
  Search,
  ChevronDown,
  Wifi,
  WifiOff,
} from "lucide-react";
import { modelApi } from "@/lib/api-endpoints";
import type { LocalModel, OpenRouterModel } from "@/lib/types";
import { API_URL, API_PREFIX, COMFYUI_HOST } from "@/lib/config";
import { cn, formatContextLength } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { Badge } from "@/components/ui/Badge";

type Health = "checking" | "ok" | "down";

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={cn("inline-block h-2.5 w-2.5 rounded-full", ok ? "bg-success" : "bg-danger")}
      style={ok ? { boxShadow: "0 0 8px rgba(48,164,108,0.7)" } : undefined}
    />
  );
}

function Section({
  title,
  icon,
  action,
  children,
}: {
  title: React.ReactNode;
  icon: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-border bg-bg-secondary/60 p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-lg text-text-primary">
          <span className="text-accent-primary">{icon}</span>
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export default function SettingsPage() {
  const [health, setHealth] = useState<Health>("checking");

  const [localModels, setLocalModels] = useState<LocalModel[]>([]);
  const [localLoading, setLocalLoading] = useState(true);
  const [localError, setLocalError] = useState(false);

  const [orModels, setOrModels] = useState<OpenRouterModel[]>([]);
  const [orCount, setOrCount] = useState(0);
  const [orLoading, setOrLoading] = useState(true);
  const [orError, setOrError] = useState(false);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const checkHealth = useCallback(async () => {
    setHealth("checking");
    try {
      const res = await modelApi.health();
      setHealth(res.status === "ok" ? "ok" : "down");
    } catch {
      setHealth("down");
    }
  }, []);

  const loadLocal = useCallback(async () => {
    setLocalLoading(true);
    setLocalError(false);
    try {
      const { data } = await modelApi.listLocal();
      setLocalModels(data);
    } catch {
      setLocalError(true);
    } finally {
      setLocalLoading(false);
    }
  }, []);

  const loadOpenRouter = useCallback(async () => {
    setOrLoading(true);
    setOrError(false);
    try {
      const { data, count } = await modelApi.listOpenRouter();
      setOrModels(data);
      setOrCount(count);
    } catch {
      setOrError(true);
    } finally {
      setOrLoading(false);
    }
  }, []);

  useEffect(() => {
    void checkHealth();
    void loadLocal();
    void loadOpenRouter();
  }, [checkHealth, loadLocal, loadOpenRouter]);

  const filtered = orModels.filter((m) => {
    const q = search.toLowerCase();
    return m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q);
  });

  const connected = health === "ok";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="border-b border-border px-6 py-4 max-md:pl-14">
        <h1 className="text-2xl text-text-primary">Settings</h1>
        <p className="text-sm text-text-secondary">Models &amp; connection</p>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-5">
          {/* Connection — live status + test button merged with config details */}
          <div className="rounded-xl border border-border bg-bg-secondary/60 px-5 py-4">
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                {connected ? (
                  <Wifi size={20} className="text-success" />
                ) : (
                  <WifiOff size={20} className="text-danger" />
                )}
                <span className="flex items-center gap-2 text-sm font-medium text-text-primary">
                  <StatusDot ok={connected} />
                  {health === "checking" ? "Checking…" : connected ? "Connected" : "Disconnected"}
                </span>
              </div>
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<RefreshCw size={14} />}
                onClick={checkHealth}
                isLoading={health === "checking"}
              >
                Test Connection
              </Button>
            </div>

            <div className="my-4 border-t border-border" />

            <dl className="grid grid-cols-1 gap-y-2 text-sm sm:grid-cols-[160px_1fr]">
              <dt className="text-text-muted">Backend URL</dt>
              <dd className="font-mono text-text-primary">{API_URL}</dd>
              <dt className="text-text-muted">API Prefix</dt>
              <dd className="font-mono text-text-primary">{API_PREFIX || "(empty)"}</dd>
              <dt className="text-text-muted">ComfyUI URL</dt>
              <dd className="font-mono text-text-primary">{COMFYUI_HOST}</dd>
            </dl>
            <p className="mt-3 text-[0.75rem] text-text-muted">
              Configuration is managed via environment variables, not through this UI.
            </p>
          </div>

          {/* Local models */}
          <Section
            title="Local Models"
            icon={<Cpu size={18} />}
            action={
              <Button
                variant="ghost"
                size="sm"
                leftIcon={<RefreshCw size={14} />}
                onClick={loadLocal}
                isLoading={localLoading}
              >
                Refresh
              </Button>
            }
          >
            {localLoading ? (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-7 w-2/3" />
                ))}
              </div>
            ) : localError ? (
              <p className="text-sm text-text-muted">
                Unable to reach local models. Start LM Studio and load a model.
              </p>
            ) : localModels.length === 0 ? (
              <p className="text-sm text-text-muted">
                No models loaded. Start LM Studio and load a model.
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {localModels.map((m) => (
                  <li
                    key={m.id}
                    className="rounded-md bg-bg-tertiary/50 px-3 py-1.5 font-mono text-[0.8125rem] text-text-primary"
                  >
                    {m.id}
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {/* OpenRouter models */}
          <Section
            title={
              <span className="flex items-center gap-2">
                OpenRouter Models
                {!orLoading && !orError && (
                  <span className="text-sm text-text-muted">({orCount} available)</span>
                )}
              </span>
            }
            icon={<Cloud size={18} />}
            action={
              <Button
                variant="ghost"
                size="sm"
                leftIcon={<RefreshCw size={14} />}
                onClick={loadOpenRouter}
                isLoading={orLoading}
              >
                Refresh
              </Button>
            }
          >
            <div className="relative mb-3">
              <Search
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
              />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search models…"
                className="h-9 w-full rounded-lg border border-border bg-bg-secondary pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted outline-none focus:ring-2 focus:ring-accent-primary"
              />
            </div>

            {orLoading ? (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : orError ? (
              <p className="text-sm text-text-muted">
                Unable to fetch OpenRouter models. Check the API key and internet connection.
              </p>
            ) : filtered.length === 0 ? (
              <p className="text-sm text-text-muted">No models match your search.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {filtered.map((m) => {
                  const open = expanded === m.id;
                  return (
                    <li
                      key={m.id}
                      className="rounded-lg border border-border bg-bg-secondary p-3"
                    >
                      <button
                        onClick={() => setExpanded(open ? null : m.id)}
                        className="flex w-full items-start justify-between gap-3 text-left"
                      >
                        <div className="flex min-w-0 flex-col gap-1">
                          <span className="truncate font-mono text-[0.8125rem] text-text-primary">
                            {m.id}
                          </span>
                          <span className="truncate text-[0.8125rem] text-text-secondary">
                            {m.name}
                          </span>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <Badge className="text-accent-secondary">
                            {formatContextLength(m.context_length)}
                          </Badge>
                          <ChevronDown
                            size={15}
                            className={cn(
                              "text-text-muted transition-transform",
                              open && "rotate-180"
                            )}
                          />
                        </div>
                      </button>
                      {m.description && (
                        <p
                          className={cn(
                            "mt-2 text-[0.8125rem] leading-relaxed text-text-muted",
                            !open && "line-clamp-2"
                          )}
                        >
                          {m.description}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </Section>

        </div>
      </div>
    </div>
  );
}
