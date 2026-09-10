import { create } from "zustand";

export type ToastKind = "success" | "error" | "info";

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

interface UIState {
  toasts: Toast[];
  pushToast: (message: string, kind?: ToastKind) => void;
  dismissToast: (id: number) => void;
}

let toastSeq = 0;

export const useUIStore = create<UIState>((set) => ({
  toasts: [],
  pushToast: (message, kind = "info") => {
    const id = ++toastSeq;
    set((s) => ({ toasts: [...s.toasts, { id, kind, message }] }));
    // auto-dismiss
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    }, 4200);
  },
  dismissToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Convenience helpers for non-component call sites. */
export const toast = {
  success: (m: string) => useUIStore.getState().pushToast(m, "success"),
  error: (m: string) => useUIStore.getState().pushToast(m, "error"),
  info: (m: string) => useUIStore.getState().pushToast(m, "info"),
};
