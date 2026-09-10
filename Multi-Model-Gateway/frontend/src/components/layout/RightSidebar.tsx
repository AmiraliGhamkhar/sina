import { useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { useLocation } from "react-router-dom";
import {
  SlidersHorizontal,
  LayoutTemplate,
  Workflow as WorkflowIcon,
  Bot,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-react";
import {
  useLayoutStore,
  RIGHT_SIDEBAR_MIN,
  RIGHT_SIDEBAR_MAX,
} from "@/stores/layout-store";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import { PresetPanel } from "@/components/settings/PresetPanel";
import { TemplatePanel } from "@/components/settings/TemplatePanel";
import { WorkflowPanel } from "@/components/settings/WorkflowPanel";
import { AgentToolsPanel } from "@/components/settings/AgentToolsPanel";
import { WorkspacePanel } from "@/components/agent/WorkspacePanel";

type Kind = "chat" | "images" | "agent";
type ImageTab = "templates" | "workflows";

/** Which contextual panel (if any) belongs to the current route. */
function kindForPath(pathname: string): Kind | null {
  if (pathname.startsWith("/images")) return "images";
  if (pathname.startsWith("/agent")) return "agent";
  if (pathname.startsWith("/chat")) return "chat";
  return null;
}

const IMAGE_TABS = [
  { id: "workflows" as const, label: "Workflows", icon: WorkflowIcon },
  { id: "templates" as const, label: "Templates", icon: LayoutTemplate },
];

export function RightSidebar() {
  const { pathname } = useLocation();
  const kind = kindForPath(pathname);

  const width = useLayoutStore((s) => s.rightSidebarWidth);
  const setWidth = useLayoutStore((s) => s.setRightSidebarWidth);
  const collapsed = useLayoutStore((s) => s.rightSidebarCollapsed);
  const toggle = useLayoutStore((s) => s.toggleRightSidebar);
  const isMobile = useIsMobile();
  const asideRef = useRef<HTMLElement>(null);
  const [imageTab, setImageTab] = useState<ImageTab>("workflows");
  // Agent panel: Tools vs Workspace. Persists within the `/agent` route session.
  type AgentTab = "tools" | "workspace";
  const [agentTab, setAgentTab] = useState<AgentTab>("tools");

  // The panel only exists for chat/image routes (settings etc. have none).
  if (!kind) return null;

  // Collapsed rail mirrors the active panel (presets / agent tools / image tab).
  const activeImageTab = IMAGE_TABS.find((t) => t.id === imageTab) ?? IMAGE_TABS[0];
  const railIcon =
    kind === "chat" ? SlidersHorizontal : kind === "agent" ? Bot : activeImageTab.icon;
  const railLabel =
    kind === "chat" ? "Presets" : kind === "agent" ? "Tools" : activeImageTab.label;
  // Title shown for the non-tabbed panels (chat → Presets, agent → Tools).
  const TitleIcon = kind === "agent" ? Bot : SlidersHorizontal;
  const titleLabel = kind === "agent" ? "Tools" : "Presets";

  // Live-resize via direct style writes during drag; commit once on mouse-up.
  // The aside is flush to the viewport's right edge, so width = innerWidth − x.
  const startResize = (e: ReactMouseEvent) => {
    e.preventDefault();
    let w = width;
    const onMove = (ev: MouseEvent) => {
      w = Math.min(
        Math.max(window.innerWidth - ev.clientX, RIGHT_SIDEBAR_MIN),
        RIGHT_SIDEBAR_MAX
      );
      if (asideRef.current) asideRef.current.style.width = `${w}px`;
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      setWidth(w);
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  const RailIcon = railIcon;

  // Mobile collapsed → a single floating button (no rail). Tapping it opens
  // the panel (presets / templates / workflows) as an overlay.
  if (collapsed && isMobile) {
    return (
      <button
        onClick={toggle}
        title={railLabel}
        aria-label={`Show ${railLabel.toLowerCase()}`}
        className="fixed right-3 top-3 z-40 flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-bg-secondary/80 text-accent-primary shadow-lg backdrop-blur transition-colors hover:bg-bg-tertiary"
      >
        <RailIcon size={18} />
      </button>
    );
  }

  // Collapsed (desktop) → a slim icon rail that expands on click.
  if (collapsed) {
    return (
      <aside className="flex h-full w-14 shrink-0 flex-col items-center border-l border-border bg-bg-secondary/80 px-2 pt-5">
        <button
          onClick={toggle}
          title={`Show ${railLabel.toLowerCase()}`}
          aria-label={`Show ${railLabel.toLowerCase()}`}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-bg-tertiary hover:text-text-primary"
        >
          <PanelRightOpen size={20} />
        </button>
        <button
          onClick={toggle}
          title={railLabel}
          aria-label={railLabel}
          className="mt-2 flex h-9 w-9 items-center justify-center rounded-lg text-accent-primary transition-colors hover:bg-bg-tertiary"
        >
          <RailIcon size={18} />
        </button>
      </aside>
    );
  }

  return (
    <>
      {/* Mobile: dim + tap-to-close backdrop behind the overlaid panel. */}
      {isMobile && (
        <div
          onClick={toggle}
          aria-hidden
          className="fixed inset-0 z-30 bg-black/50 backdrop-blur-sm"
        />
      )}
      <aside
        ref={asideRef}
        style={{ width: isMobile ? "min(85vw, 340px)" : width }}
        className={cn(
          "flex h-full shrink-0 flex-col border-l border-border bg-bg-secondary/80",
          // On mobile the panel floats over the content instead of squeezing it.
          isMobile ? "fixed inset-y-0 right-0 z-40 shadow-2xl" : "relative"
        )}
      >
      {/* Header — chat title, agent Tools|Workspace toggle, or image Templates|Workflows toggle */}
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        {kind === "agent" ? (
          <div className="flex gap-1 rounded-lg border border-border bg-bg-primary/40 p-0.5">
            {(["tools", "workspace"] as const).map((id) => (
              <button
                key={id}
                onClick={() => setAgentTab(id)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-[0.8125rem] font-medium capitalize transition-colors",
                  agentTab === id
                    ? "bg-bg-elevated text-text-primary shadow-[0_1px_3px_rgba(0,0,0,0.35)]"
                    : "text-text-secondary hover:text-text-primary"
                )}
              >
                {id}
              </button>
            ))}
          </div>
        ) : kind === "images" ? (
          <div className="flex gap-1 rounded-lg border border-border bg-bg-primary/40 p-0.5">
            {IMAGE_TABS.map(({ id, label, icon: TabIcon }) => (
              <button
                key={id}
                onClick={() => setImageTab(id)}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[0.8125rem] font-medium transition-colors",
                  imageTab === id
                    ? "bg-bg-elevated text-text-primary shadow-[0_1px_3px_rgba(0,0,0,0.35)]"
                    : "text-text-secondary hover:text-text-primary"
                )}
              >
                <TabIcon size={13} className={imageTab === id ? "text-accent-primary" : undefined} />
                {label}
              </button>
            ))}
          </div>
        ) : (
          <>
            <TitleIcon size={16} className="shrink-0 text-accent-primary" />
            <span className="font-serif text-[1.15rem] leading-none text-text-primary">{titleLabel}</span>
          </>
        )}
        <button
          onClick={toggle}
          className="ml-auto flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-muted transition-colors hover:bg-bg-tertiary hover:text-text-primary"
          title="Collapse panel"
          aria-label="Collapse panel"
        >
          <PanelRightClose size={17} />
        </button>
      </div>

      {/* Content */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {kind === "chat" ? (
          <PresetPanel />
        ) : kind === "agent" ? (
          agentTab === "workspace" ? (
            <WorkspacePanel />
          ) : (
            <AgentToolsPanel />
          )
        ) : imageTab === "templates" ? (
          <TemplatePanel />
        ) : (
          <WorkflowPanel />
        )}
      </div>

      {/* Drag-to-resize handle on the border shared with the main area (desktop only) */}
      {!isMobile && (
        <div
          onMouseDown={startResize}
          className="absolute left-0 top-0 z-20 h-full w-1.5 cursor-col-resize transition-colors hover:bg-accent-primary/40 active:bg-accent-primary/60"
          title="Drag to resize"
        />
      )}
      </aside>
    </>
  );
}
