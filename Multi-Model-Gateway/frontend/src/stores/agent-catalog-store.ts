import { create } from "zustand";
import { agentsApi } from "@/lib/api-endpoints";
import type { Agent, AgentCreate, AgentUpdate } from "@/lib/types";

interface AgentCatalogState {
  agents: Agent[];
  isLoading: boolean;
  hasLoaded: boolean;
  selectedId: string | null;

  fetchAgents: () => Promise<void>;
  createAgent: (data: AgentCreate) => Promise<Agent>;
  updateAgent: (id: string, data: AgentUpdate) => Promise<Agent>;
  deleteAgent: (id: string) => Promise<void>;
  select: (id: string | null) => void;
  suggest: (goal: string, description?: string) => Promise<{ name: string; description: string; system_prompt: string; suggested_tools: string[]; suggested_model: string | null }>;
}

export const useAgentCatalogStore = create<AgentCatalogState>((set) => ({
  agents: [],
  isLoading: false,
  hasLoaded: false,
  selectedId: null,

  fetchAgents: async () => {
    set({ isLoading: true });
    try {
      const { data } = await agentsApi.list();
      set({ agents: data, hasLoaded: true });
    } finally {
      set({ isLoading: false });
    }
  },

  createAgent: async (data) => {
    const agent = await agentsApi.create(data);
    set((state) => ({ agents: [agent, ...state.agents], selectedId: agent.id }));
    return agent;
  },

  updateAgent: async (id, data) => {
    const agent = await agentsApi.update(id, data);
    set((state) => ({ agents: state.agents.map((a) => (a.id === id ? agent : a)) }));
    return agent;
  },

  deleteAgent: async (id) => {
    await agentsApi.delete(id);
    set((state) => ({
      agents: state.agents.filter((a) => a.id !== id),
      selectedId: state.selectedId === id ? null : state.selectedId,
    }));
  },

  select: (id) => set({ selectedId: id }),

  suggest: async (goal, description) => {
    return agentsApi.suggest(goal, description);
  },
}));
