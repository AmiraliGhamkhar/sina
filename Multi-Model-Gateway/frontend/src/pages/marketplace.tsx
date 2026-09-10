import { useEffect } from "react";
import { Store, Download, Globe } from "lucide-react";
import { marketplaceApi } from "@/lib/api-endpoints";
import { toast } from "@/stores/ui-store";
import { ApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useState } from "react";

export default function MarketplacePage() {
  const [agents, setAgents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [installs, setInstalls] = useState<Set<string>>(new Set());

  const fetchMarketplace = async () => {
    setLoading(true);
    try {
      const { data } = await marketplaceApi.list();
      setAgents(data);
      const inst = await marketplaceApi.myInstalls();
      setInstalls(new Set(inst.data.map((r: any) => r.agent_id)));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Could not load marketplace.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchMarketplace();
  }, []);

  const install = async (id: string) => {
    try {
      await marketplaceApi.install(id);
      setInstalls((s) => new Set([...s, id]));
      toast.success("Installed — pinned to latest version.");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Install failed.");
    }
  };

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-6">
      <div className="flex items-center gap-2">
        <Store size={20} className="text-accent-primary" />
        <h1 className="text-xl font-semibold text-text-primary">Marketplace</h1>
        <span className="text-sm text-text-muted">— public agents</span>
      </div>
      {loading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : agents.length === 0 ? (
        <p className="py-12 text-center text-sm text-text-muted">No public agents yet.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {agents.map((a: any) => {
            const installed = installs.has(a.id);
            return (
              <div key={a.id} className="flex items-start gap-3 rounded-lg border border-border bg-bg-secondary/40 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium text-text-primary">{a.name}</span>
                    <span className="rounded bg-bg-elevated/60 px-1.5 py-0.5 font-mono text-[0.7rem] text-text-secondary">v{a.version}</span>
                    <Globe size={12} className="text-text-muted" />
                  </div>
                  {a.description && <p className="truncate text-xs text-text-muted">{a.description}</p>}
                </div>
                {installed ? (
                  <span className="shrink-0 rounded bg-emerald-500/15 px-3 py-1.5 text-xs text-emerald-600">Installed</span>
                ) : (
                  <Button size="sm" leftIcon={<Download size={14} />} onClick={() => install(a.id)}>
                    Install
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
