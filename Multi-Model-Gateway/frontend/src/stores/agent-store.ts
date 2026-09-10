import { create } from "zustand";
import { agentApi } from "@/lib/api-endpoints";
import type { AgentToolInfo } from "@/lib/types";

interface AgentToolsState {
  tools: AgentToolInfo[];
  isLoading: boolean;
  hasLoaded: boolean;

  fetchTools: () => Promise<void>;
  setPermission: (name: string, allowed: boolean) => Promise<void>;
}

export const useAgentStore = create<AgentToolsState>((set) => ({
  tools: [],
  isLoading: false,
  hasLoaded: false,

  fetchTools: async () => {
    set({ isLoading: true });
    try {
      const { data } = await agentApi.tools();
      set({ tools: data, hasLoaded: true });
    } finally {
      set({ isLoading: false });
    }
  },

  setPermission: async (name, allowed) => {
    // Optimistic flip; revert if the PUT fails.
    set((s) => ({ tools: s.tools.map((t) => (t.name === name ? { ...t, allowed } : t)) }));
    try {
      await agentApi.setPermission(name, allowed);
    } catch (err) {
      set((s) => ({
        tools: s.tools.map((t) => (t.name === name ? { ...t, allowed: !allowed } : t)),
      }));
      throw err;
    }
  },
}));
