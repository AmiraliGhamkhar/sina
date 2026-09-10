import { ProviderPanel } from "@/components/settings/ProviderPanel";

export default function ProvidersPage() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-border px-6 py-4 max-md:pl-14">
        <div>
          <h1 className="text-2xl text-text-primary">Providers</h1>
          <p className="text-sm text-text-secondary">Bring-your-own-key model providers</p>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-3xl">
          <ProviderPanel />
        </div>
      </div>
    </div>
  );
}
