import { create } from "zustand";
import { workflowApi } from "@/lib/api-endpoints";
import type { Workflow, WorkflowCreate, WorkflowUpdate } from "@/lib/types";

interface WorkflowState {
  workflows: Workflow[];
  isLoading: boolean;
  hasLoaded: boolean;

  fetchWorkflows: () => Promise<void>;
  createWorkflow: (data: WorkflowCreate) => Promise<Workflow>;
  updateWorkflow: (id: string, data: WorkflowUpdate) => Promise<Workflow>;
  deleteWorkflow: (id: string) => Promise<void>;
}

export const useWorkflowStore = create<WorkflowState>((set) => ({
  workflows: [],
  isLoading: false,
  hasLoaded: false,

  fetchWorkflows: async () => {
    set({ isLoading: true });
    try {
      const { data } = await workflowApi.list();
      set({ workflows: data, hasLoaded: true });
    } finally {
      set({ isLoading: false });
    }
  },

  createWorkflow: async (data) => {
    const workflow = await workflowApi.create(data);
    set((state) => ({ workflows: [...state.workflows, workflow] }));
    return workflow;
  },

  updateWorkflow: async (id, data) => {
    const workflow = await workflowApi.update(id, data);
    set((state) => ({
      workflows: state.workflows.map((w) => (w.id === id ? workflow : w)),
    }));
    return workflow;
  },

  deleteWorkflow: async (id) => {
    await workflowApi.delete(id);
    set((state) => ({
      workflows: state.workflows.filter((w) => w.id !== id),
    }));
  },
}));
