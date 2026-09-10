import { create } from "zustand";
import { researchApi } from "@/lib/api-endpoints";
import type { ResearchJob } from "@/lib/types";

interface ResearchState {
  jobs: ResearchJob[];
  isLoading: boolean;
  hasLoaded: boolean;

  fetchJobs: () => Promise<void>;
  createJob: (query: string, provider?: string, model?: string) => Promise<string>;
  cancelJob: (id: string) => Promise<void>;
  /** Merge live progress / status into the cached list item. */
  patchJob: (id: string, patch: Partial<ResearchJob>) => void;
}

export const useResearchStore = create<ResearchState>((set, get) => ({
  jobs: [],
  isLoading: false,
  hasLoaded: false,

  fetchJobs: async () => {
    set({ isLoading: true });
    try {
      const { data } = await researchApi.list();
      set({ jobs: data, hasLoaded: true });
    } finally {
      set({ isLoading: false });
    }
  },

  createJob: async (query, provider, model) => {
    const { job_id } = await researchApi.create(query, provider, model);
    await get().fetchJobs();
    return job_id;
  },

  cancelJob: async (id) => {
    await researchApi.cancel(id);
    await get().fetchJobs();
  },

  patchJob: (id, patch) =>
    set((s) => ({ jobs: s.jobs.map((j) => (j.id === id ? { ...j, ...patch } : j)) })),
}));
