import { create } from "zustand";
import { presetApi } from "@/lib/api-endpoints";
import type { Preset, PresetCreate, PresetUpdate } from "@/lib/types";

interface PresetState {
  presets: Preset[];
  isLoading: boolean;
  hasLoaded: boolean;

  fetchPresets: () => Promise<void>;
  createPreset: (data: PresetCreate) => Promise<Preset>;
  updatePreset: (id: string, data: PresetUpdate) => Promise<Preset>;
  deletePreset: (id: string) => Promise<void>;
}

export const usePresetStore = create<PresetState>((set) => ({
  presets: [],
  isLoading: false,
  hasLoaded: false,

  fetchPresets: async () => {
    set({ isLoading: true });
    try {
      const { data } = await presetApi.list();
      set({ presets: data, hasLoaded: true });
    } finally {
      set({ isLoading: false });
    }
  },

  createPreset: async (data) => {
    const preset = await presetApi.create(data);
    set((state) => ({ presets: [...state.presets, preset] }));
    return preset;
  },

  updatePreset: async (id, data) => {
    const preset = await presetApi.update(id, data);
    set((state) => ({
      presets: state.presets.map((p) => (p.id === id ? preset : p)),
    }));
    return preset;
  },

  deletePreset: async (id) => {
    await presetApi.delete(id);
    set((state) => ({
      presets: state.presets.filter((p) => p.id !== id),
    }));
  },
}));
