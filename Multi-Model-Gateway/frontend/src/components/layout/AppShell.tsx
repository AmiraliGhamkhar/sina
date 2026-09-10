import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { RightSidebar } from "./RightSidebar";
import { useChatStore } from "@/stores/chat-store";
import { usePresetStore } from "@/stores/preset-store";
import { useTemplateStore } from "@/stores/template-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import { useLayoutStore } from "@/stores/layout-store";
import { useIsMobile } from "@/hooks/use-mobile";

export function AppShell() {
  const fetchConversations = useChatStore((s) => s.fetchConversations);
  const fetchPresets = usePresetStore((s) => s.fetchPresets);
  const fetchTemplates = useTemplateStore((s) => s.fetchTemplates);
  const fetchWorkflows = useWorkflowStore((s) => s.fetchWorkflows);

  const isMobile = useIsMobile();
  const setSidebarCollapsed = useLayoutStore((s) => s.setSidebarCollapsed);
  const setRightSidebarCollapsed = useLayoutStore((s) => s.setRightSidebarCollapsed);

  // Warm shared data once after authentication.
  useEffect(() => {
    void fetchConversations();
    void fetchPresets();
    void fetchTemplates();
    void fetchWorkflows();
  }, [fetchConversations, fetchPresets, fetchTemplates, fetchWorkflows]);

  // On mobile both panels start collapsed so the content gets the full width;
  // re-collapses whenever the viewport crosses into mobile (not on every render,
  // so the user can still open a panel during a session).
  useEffect(() => {
    if (isMobile) {
      setSidebarCollapsed(true);
      setRightSidebarCollapsed(true);
    }
  }, [isMobile, setSidebarCollapsed, setRightSidebarCollapsed]);

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <Outlet />
      </main>
      <RightSidebar />
    </div>
  );
}
