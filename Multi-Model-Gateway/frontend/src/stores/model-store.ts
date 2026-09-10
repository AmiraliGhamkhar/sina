import { create } from "zustand";
import { modelApi } from "@/lib/api-endpoints";
import type { LocalModel, OpenRouterModel } from "@/lib/types";

interface ModelState {
  localModels: LocalModel[];
  openrouterModels: OpenRouterModel[];
  loadingLocal: boolean;
  loadingOpenRouter: boolean;
  loadedLocal: boolean;
  loadedOpenRouter: boolean;
  fetchLocal: () => Promise<void>;
  fetchOpenRouter: () => Promise<void>;
}

// Lazily-loaded, cached model lists. Each provider is fetched once (on first
// hover of its row in the ModelSelector).
export const useModelStore = create<ModelState>((set, get) => ({
  localModels: [],
  openrouterModels: [],
  loadingLocal: false,
  loadingOpenRouter: false,
  loadedLocal: false,
  loadedOpenRouter: false,

  fetchLocal: async () => {
    if (get().loadedLocal || get().loadingLocal) return;
    set({ loadingLocal: true });
    try {
      const res = await modelApi.listLocal();
      set({ localModels: res.data ?? [], loadedLocal: true });
    } catch {
      /* leave empty — the flyout shows "No models found" */
    } finally {
      set({ loadingLocal: false });
    }
  },

  fetchOpenRouter: async () => {
    if (get().loadedOpenRouter || get().loadingOpenRouter) return;
    set({ loadingOpenRouter: true });
    try {
      const res = await modelApi.listOpenRouter();
      set({ openrouterModels: res.data ?? [], loadedOpenRouter: true });
    } catch {
      /* leave empty */
    } finally {
      set({ loadingOpenRouter: false });
    }
  },
}));
