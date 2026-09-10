import { create } from "zustand";
import { persist } from "zustand/middleware";

export const SIDEBAR_MIN = 220;
export const SIDEBAR_MAX = 480;

export const RIGHT_SIDEBAR_MIN = 280;
export const RIGHT_SIDEBAR_MAX = 460;

interface LayoutState {
  sidebarWidth: number;
  sidebarCollapsed: boolean;
  setSidebarWidth: (w: number) => void;
  toggleSidebar: () => void;
  setSidebarCollapsed: (v: boolean) => void;

  rightSidebarWidth: number;
  rightSidebarCollapsed: boolean;
  setRightSidebarWidth: (w: number) => void;
  toggleRightSidebar: () => void;
  setRightSidebarCollapsed: (v: boolean) => void;
}

export const useLayoutStore = create<LayoutState>()(
  persist(
    (set) => ({
      sidebarWidth: 320,
      sidebarCollapsed: false,
      setSidebarWidth: (w) =>
        set({ sidebarWidth: Math.min(Math.max(w, SIDEBAR_MIN), SIDEBAR_MAX) }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),

      rightSidebarWidth: 340,
      rightSidebarCollapsed: false,
      setRightSidebarWidth: (w) =>
        set({
          rightSidebarWidth: Math.min(Math.max(w, RIGHT_SIDEBAR_MIN), RIGHT_SIDEBAR_MAX),
        }),
      toggleRightSidebar: () =>
        set((s) => ({ rightSidebarCollapsed: !s.rightSidebarCollapsed })),
      setRightSidebarCollapsed: (v) => set({ rightSidebarCollapsed: v }),
    }),
    { name: "llm-gateway-layout" }
  )
);
