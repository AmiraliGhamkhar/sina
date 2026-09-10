import { create } from "zustand";
import { workspaceApi } from "@/lib/api-endpoints";
import type { FileEdit } from "@/lib/types";

interface WorkspaceState {
  files: string[];
  edits: FileEdit[];
  isLoading: boolean;
  hasLoaded: boolean;
  agentId: string | null;

  fetchFiles: (agentId: string, path?: string) => Promise<void>;
  fetchEdits: (agentId: string, params?: { limit?: number; offset?: number }) => Promise<void>;
  fetchAll: (agentId: string) => Promise<void>;
  undo: (agentId: string, editId: string) => Promise<void>;
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  files: [],
  edits: [],
  isLoading: false,
  hasLoaded: false,
  agentId: null,

  fetchFiles: async (agentId, path = ".") => {
    const { files } = await workspaceApi.files(agentId, path);
    set({ files, agentId });
  },

  fetchEdits: async (agentId, params) => {
    const { data } = await workspaceApi.edits(agentId, params);
    set({ edits: data, agentId });
  },

  fetchAll: async (agentId) => {
    set({ isLoading: true });
    try {
      const [f, e] = await Promise.all([
        workspaceApi.files(agentId),
        workspaceApi.edits(agentId, { limit: 50 }),
      ]);
      set({ files: f.files, edits: e.data, hasLoaded: true, agentId });
    } finally {
      set({ isLoading: false });
    }
  },

  undo: async (agentId, editId) => {
    await workspaceApi.undo(agentId, editId);
    // Refresh edits + files after undo (new commit + audit row).
    const { fetchAll } = get();
    await fetchAll(agentId);
  },
}));
