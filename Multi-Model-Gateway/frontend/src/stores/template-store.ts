import { create } from "zustand";
import { templateApi } from "@/lib/api-endpoints";
import type {
  PromptTemplate,
  TemplateCreate,
  TemplateUpdate,
} from "@/lib/types";

interface TemplateState {
  templates: PromptTemplate[];
  isLoading: boolean;
  hasLoaded: boolean;

  fetchTemplates: () => Promise<void>;
  createTemplate: (data: TemplateCreate) => Promise<PromptTemplate>;
  updateTemplate: (id: string, data: TemplateUpdate) => Promise<PromptTemplate>;
  deleteTemplate: (id: string) => Promise<void>;
}

export const useTemplateStore = create<TemplateState>((set) => ({
  templates: [],
  isLoading: false,
  hasLoaded: false,

  fetchTemplates: async () => {
    set({ isLoading: true });
    try {
      const { data } = await templateApi.list();
      set({ templates: data, hasLoaded: true });
    } finally {
      set({ isLoading: false });
    }
  },

  createTemplate: async (data) => {
    const template = await templateApi.create(data);
    set((state) => ({ templates: [...state.templates, template] }));
    return template;
  },

  updateTemplate: async (id, data) => {
    const template = await templateApi.update(id, data);
    set((state) => ({
      templates: state.templates.map((t) => (t.id === id ? template : t)),
    }));
    return template;
  },

  deleteTemplate: async (id) => {
    await templateApi.delete(id);
    set((state) => ({
      templates: state.templates.filter((t) => t.id !== id),
    }));
  },
}));
