import { create } from "zustand";
import { loadHistory, addHistory, clearHistory } from "@/lib/image-history";
import type { HistoryEntry } from "@/lib/image-history";

interface ImageState {
  // Bumped to request a fresh image composer (clears the prompt + last results).
  // The images page watches this nonce and resets on change.
  newImageNonce: number;
  startNewImage: () => void;

  // Generation history (persisted in localStorage via lib/image-history).
  // Lifted into the store so the images page and the left sidebar stay in sync.
  history: HistoryEntry[];
  addImageHistory: (entry: HistoryEntry) => void;
  clearImageHistory: () => void;
}

export const useImageStore = create<ImageState>((set) => ({
  newImageNonce: 0,
  startNewImage: () => set((s) => ({ newImageNonce: s.newImageNonce + 1 })),

  history: loadHistory(),
  addImageHistory: (entry) => set({ history: addHistory(entry) }),
  clearImageHistory: () => set({ history: clearHistory() }),
}));
