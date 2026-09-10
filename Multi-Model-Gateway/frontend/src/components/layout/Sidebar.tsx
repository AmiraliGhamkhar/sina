import { useRef, type MouseEvent as ReactMouseEvent } from "react";
import { NavLink, useNavigate, useLocation } from "react-router-dom";
import {
  Brain,
  MessageSquarePlus,
  ImagePlus,
  ImageIcon,
  Bot,
  Telescope,
  Settings,
  LogOut,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  KeyRound,
  Library,
} from "lucide-react";
import { useLayoutStore, SIDEBAR_MIN, SIDEBAR_MAX } from "@/stores/layout-store";
import { useIsMobile } from "@/hooks/use-mobile";
import { useAuthStore } from "@/stores/auth-store";
import { useChatStore } from "@/stores/chat-store";
import { useImageStore } from "@/stores/image-store";
import { toast } from "@/stores/ui-store";
import { cn } from "@/lib/utils";
import { ConversationList } from "@/components/chat/ConversationList";
import { ImageHistoryList } from "@/components/images/ImageHistoryList";

// Top-level mode switch — the Chat ↔ Image distinction.
const MODES = [
  { to: "/chat", label: "Chat", icon: MessagesSquare },
  { to: "/images", label: "Image", icon: ImageIcon },
];

// Secondary destinations — agent + research + models + providers.
const TOOLS = [
  { to: "/agent", label: "Agent", icon: Bot },
  { to: "/agents", label: "Agents", icon: Bot },
  { to: "/marketplace", label: "Marketplace", icon: Library },
  { to: "/research", label: "Research", icon: Telescope },
  { to: "/models", label: "Models", icon: Library },
  { to: "/providers", label: "Providers", icon: KeyRound },
];

export function Sidebar() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const userEmail = useAuthStore((s) => s.userEmail);
  const logout = useAuthStore((s) => s.logout);
  const startNewChat = useChatStore((s) => s.startNewChat);
  const startNewImage = useImageStore((s) => s.startNewImage);

  // The primary action mirrors the active mode: New Chat vs. New Image.
  const isImages = pathname.startsWith("/images");
  const newLabel = isImages ? "New Image" : "New Chat";
  const NewIcon = isImages ? ImagePlus : MessageSquarePlus;

  const sidebarWidth = useLayoutStore((s) => s.sidebarWidth);
  const setSidebarWidth = useLayoutStore((s) => s.setSidebarWidth);
  const toggleSidebar = useLayoutStore((s) => s.toggleSidebar);
  const collapsed = useLayoutStore((s) => s.sidebarCollapsed);
  const isMobile = useIsMobile();
  const asideRef = useRef<HTMLElement>(null);

  // Live-resize via direct style writes during drag; commit once on mouse-up.
  const startResize = (e: ReactMouseEvent) => {
    e.preventDefault();
    let w = sidebarWidth;
    const onMove = (ev: MouseEvent) => {
      w = Math.min(Math.max(ev.clientX, SIDEBAR_MIN), SIDEBAR_MAX);
      if (asideRef.current) asideRef.current.style.width = `${w}px`;
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      setSidebarWidth(w);
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  const handleNew = () => {
    if (isImages) {
      startNewImage();
      navigate("/images");
    } else {
      startNewChat();
      navigate("/chat");
    }
  };

  const handleLogout = async () => {
    await logout();
    toast.info("Signed out.");
    navigate("/login");
  };

  // Mobile collapsed → a single floating button (no rail), so the chat gets
  // the full width. Tapping it opens the panel as an overlay.
  if (collapsed && isMobile) {
    return (
      <button
        onClick={toggleSidebar}
        title="Open menu"
        aria-label="Open menu"
        className="fixed left-3 top-3 z-40 flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-bg-secondary/80 text-text-secondary shadow-lg backdrop-blur transition-colors hover:bg-bg-tertiary hover:text-text-primary"
      >
        <PanelLeftOpen size={19} />
      </button>
    );
  }

  // Collapsed (desktop) → a slim icon rail that mirrors the expanded layout's
  // vertical positions (new chat + mode toggle at top, settings/account footer).
  if (collapsed) {
    return (
      <aside className="flex h-full w-14 shrink-0 flex-col border-r border-border bg-bg-secondary/80">
        {/* top: expand (in place of the logo) + new chat */}
        <div className="flex flex-col items-center gap-2 px-2 pt-5 pb-3">
          <button
            onClick={toggleSidebar}
            title="Expand sidebar"
            aria-label="Expand sidebar"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-bg-tertiary hover:text-text-primary"
          >
            <PanelLeftOpen size={20} />
          </button>
          <button
            onClick={handleNew}
            title={newLabel}
            aria-label={newLabel}
            className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-primary text-white transition-[filter] hover:brightness-110"
          >
            <NewIcon size={18} />
          </button>
        </div>

        {/* mode toggle — Chat / Image */}
        <nav className="flex flex-col items-center gap-1 px-2 pb-2">
          {MODES.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              title={label}
              className={({ isActive }) =>
                cn(
                  "flex h-9 w-9 items-center justify-center rounded-lg transition-colors",
                  isActive
                    ? "bg-bg-tertiary text-accent-primary"
                    : "text-text-secondary hover:bg-bg-tertiary/60 hover:text-text-primary"
                )
              }
            >
              <Icon size={18} />
            </NavLink>
          ))}
        </nav>

        {/* secondary nav — Agent / Research / Models */}
        <nav className="flex flex-col items-center gap-1 border-t border-border px-2 py-2">
          {TOOLS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              title={label}
              className={({ isActive }) =>
                cn(
                  "flex h-9 w-9 items-center justify-center rounded-lg transition-colors",
                  isActive
                    ? "bg-bg-tertiary text-accent-primary"
                    : "text-text-secondary hover:bg-bg-tertiary/60 hover:text-text-primary"
                )
              }
            >
              <Icon size={18} />
            </NavLink>
          ))}
        </nav>

        {/* spacer where the conversation list sits when expanded */}
        <div className="min-h-0 flex-1" />

        {/* footer — settings + account */}
        <div className="flex flex-col items-center gap-1 border-t border-border px-2 py-3">
          <NavLink
            to="/settings"
            title="Settings"
            className={({ isActive }) =>
              cn(
                "flex h-9 w-9 items-center justify-center rounded-lg transition-colors",
                isActive
                  ? "bg-bg-tertiary text-accent-primary"
                  : "text-text-secondary hover:bg-bg-tertiary/60 hover:text-text-primary"
              )
            }
          >
            <Settings size={18} />
          </NavLink>
          <button
            onClick={toggleSidebar}
            title={userEmail ?? "Account"}
            aria-label="Account"
            className="flex h-9 w-9 items-center justify-center rounded-full bg-bg-elevated text-[0.7rem] font-semibold uppercase text-text-primary transition-colors hover:bg-bg-tertiary"
          >
            {userEmail?.[0] ?? "?"}
          </button>
        </div>
      </aside>
    );
  }

  return (
    <>
      {/* Mobile: dim + tap-to-close backdrop behind the overlaid panel. */}
      {isMobile && (
        <div
          onClick={toggleSidebar}
          aria-hidden
          className="fixed inset-0 z-30 bg-black/50 backdrop-blur-sm"
        />
      )}
      <aside
        ref={asideRef}
        style={{ width: isMobile ? "min(85vw, 320px)" : sidebarWidth }}
        className={cn(
          "flex h-full shrink-0 flex-col border-r border-border bg-bg-secondary/80",
          // On mobile the panel floats over the content instead of squeezing it.
          isMobile ? "fixed inset-y-0 left-0 z-40 shadow-2xl" : "relative"
        )}
      >
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 pt-5 pb-4">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-bg-tertiary">
          <Brain size={18} className="text-accent-primary" />
        </span>
        <span className="font-serif text-[1.35rem] leading-none text-text-primary">
          llm-gateway
        </span>
        <button
          onClick={toggleSidebar}
          className="ml-auto flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-muted transition-colors hover:bg-bg-tertiary hover:text-text-primary"
          title="Collapse sidebar"
          aria-label="Collapse sidebar"
        >
          <PanelLeftClose size={17} />
        </button>
      </div>

      {/* Mode toggle — Chat / Image */}
      <div className="px-3 pb-3">
        <div className="flex gap-1 rounded-xl border border-border bg-bg-primary/40 p-1">
          {MODES.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-bg-elevated text-text-primary shadow-[0_1px_3px_rgba(0,0,0,0.35)]"
                    : "text-text-secondary hover:text-text-primary"
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon size={15} className={isActive ? "text-accent-primary" : undefined} />
                  {label}
                </>
              )}
            </NavLink>
          ))}
        </div>
      </div>

      {/* Secondary nav — Agent / Research / Models */}
      <nav className="flex flex-col gap-0.5 px-3 pb-3">
        {TOOLS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-bg-tertiary text-text-primary"
                  : "text-text-secondary hover:bg-bg-tertiary/60 hover:text-text-primary"
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon size={16} className={isActive ? "text-accent-primary" : "text-text-muted"} />
                {label}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* New chat */}
      <div className="px-3 pb-3">
        <button
          onClick={handleNew}
          className={cn(
            "flex w-full items-center justify-center gap-2 rounded-lg bg-accent-primary px-4 py-2.5",
            "text-sm font-medium text-white transition-[transform,filter] duration-150",
            "hover:brightness-110 active:scale-[0.98] shadow-[0_2px_12px_-2px_rgba(255,101,63,0.5)]"
          )}
        >
          <NewIcon size={17} />
          {newLabel}
        </button>
      </div>

      {/* History — chat conversations, or image generations in Image mode */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2">
        {isImages ? <ImageHistoryList /> : <ConversationList />}
      </div>

      {/* Footer / user + settings + logout */}
      <div className="flex items-center justify-between gap-2 border-t border-border px-3 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-bg-elevated text-[0.7rem] font-semibold uppercase text-text-primary">
            {userEmail?.[0] ?? "?"}
          </span>
          <span className="truncate text-[0.8125rem] text-text-secondary" title={userEmail ?? undefined}>
            {userEmail ?? "Unknown"}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <NavLink
            to="/settings"
            title="Settings"
            aria-label="Settings"
            className={({ isActive }) =>
              cn(
                "flex h-8 w-8 items-center justify-center rounded-md transition-colors",
                isActive
                  ? "bg-bg-tertiary text-accent-primary"
                  : "text-text-muted hover:bg-bg-tertiary hover:text-text-primary"
              )
            }
          >
            <Settings size={16} />
          </NavLink>
          <button
            onClick={handleLogout}
            className="flex h-8 w-8 items-center justify-center rounded-md text-text-muted hover:bg-bg-tertiary hover:text-danger transition-colors"
            aria-label="Log out"
            title="Log out"
          >
            <LogOut size={16} />
          </button>
        </div>
      </div>

      {/* Drag-to-resize handle on the border shared with the main area (desktop only) */}
      {!isMobile && (
        <div
          onMouseDown={startResize}
          className="absolute right-0 top-0 z-20 h-full w-1.5 cursor-col-resize transition-colors hover:bg-accent-primary/40 active:bg-accent-primary/60"
          title="Drag to resize"
        />
      )}
      </aside>
    </>
  );
}
