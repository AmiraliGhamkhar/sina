import { create } from "zustand";
import { providerApi } from "@/lib/api-endpoints";
import type {
  ProviderCreate,
  ProviderRow,
  ProviderUpdate,
} from "@/lib/types";

/** Stable display order: local first, then cloud; alphabetical within a role. */
function sortProviders(rows: ProviderRow[]): ProviderRow[] {
  return [...rows].sort((a, b) => {
    if (a.role !== b.role) return a.role === "local" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

interface ProviderState {
  providers: ProviderRow[];
  isLoading: boolean;
  hasLoaded: boolean;

  fetchProviders: () => Promise<void>;
  createProvider: (data: ProviderCreate) => Promise<ProviderRow>;
  updateProvider: (id: string, data: ProviderUpdate) => Promise<ProviderRow>;
  deleteProvider: (id: string) => Promise<void>;
}

export const useProviderStore = create<ProviderState>((set) => ({
  providers: [],
  isLoading: false,
  hasLoaded: false,

  fetchProviders: async () => {
    set({ isLoading: true });
    try {
      const { data } = await providerApi.list();
      set({ providers: sortProviders(data), hasLoaded: true });
    } finally {
      set({ isLoading: false });
    }
  },

  createProvider: async (data) => {
    const provider = await providerApi.create(data);
    set((state) => ({
      // The backend clears every other row in the role when a new default is
      // created — mirror that so the list never shows two defaults.
      providers: sortProviders(
        state.providers
          .map((p) =>
            provider.is_default && p.role === provider.role
              ? { ...p, is_default: false }
              : p
          )
          .concat(provider)
      ),
    }));
    return provider;
  },

  updateProvider: async (id, data) => {
    const provider = await providerApi.update(id, data);
    set((state) => ({
      providers: state.providers.map((p) => {
        if (p.id === id) return provider;
        // Same single-default-per-role invariant as create.
        if (provider.is_default && p.role === provider.role) {
          return { ...p, is_default: false };
        }
        return p;
      }),
    }));
    return provider;
  },

  deleteProvider: async (id) => {
    await providerApi.delete(id);
    set((state) => ({
      providers: state.providers.filter((p) => p.id !== id),
    }));
  },
}));
