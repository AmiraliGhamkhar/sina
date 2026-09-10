import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MoreVertical, Pencil, Trash2 } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { toast } from "@/stores/ui-store";
import { cn, formatRelativeTime, formatCompact } from "@/lib/utils";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

export function ConversationList() {
  const navigate = useNavigate();
  const activeId = useChatStore((s) => s.activeConversationId);
  const conversations = useChatStore((s) => s.conversations);
  const loading = useChatStore((s) => s.loadingConversations);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);

  const [menuId, setMenuId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const editRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId) editRef.current?.focus();
  }, [editingId]);

  useEffect(() => {
    if (!menuId) return;
    const close = () => setMenuId(null);
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [menuId]);

  const startRename = (id: string, current: string) => {
    setEditingId(id);
    setEditValue(current);
    setMenuId(null);
  };

  const commitRename = async (id: string) => {
    const title = editValue.trim();
    setEditingId(null);
    if (!title) return;
    const original = conversations.find((c) => c.id === id)?.title;
    if (title === original) return;
    try {
      await renameConversation(id, title);
    } catch {
      toast.error("Could not rename conversation.");
    }
  };

  const confirmDelete = async () => {
    if (!deleteId) return;
    setDeleting(true);
    try {
      const wasActive = deleteId === activeId;
      await deleteConversation(deleteId);
      toast.success("Conversation deleted.");
      if (wasActive) navigate("/chat");
    } catch {
      toast.error("Could not delete conversation.");
    } finally {
      setDeleting(false);
      setDeleteId(null);
    }
  };

  if (loading && conversations.length === 0) {
    return (
      <div className="flex flex-col gap-1.5 px-1 py-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (conversations.length === 0) {
    return (
      <p className="px-3 py-8 text-center text-sm text-text-muted">
        No conversations yet
      </p>
    );
  }

  return (
    <>
      <ul className="flex flex-col gap-0.5 py-1">
        {conversations.map((c, i) => {
          const isActive = c.id === activeId;
          const isEditing = c.id === editingId;
          return (
            <li
              key={c.id}
              className="animate-fade-in"
              style={{ animationDelay: `${Math.min(i, 12) * 30}ms` }}
            >
              <div
                className={cn(
                  "group relative flex items-center rounded-lg border-l-2 transition-colors",
                  isActive
                    ? "border-accent-primary bg-bg-tertiary"
                    : "border-transparent hover:bg-bg-tertiary/60"
                )}
              >
                {isEditing ? (
                  <input
                    ref={editRef}
                    value={editValue}
                    onChange={(e) => setEditValue(e.target.value)}
                    onBlur={() => commitRename(c.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitRename(c.id);
                      if (e.key === "Escape") setEditingId(null);
                    }}
                    className="w-full rounded-md bg-bg-primary px-2.5 py-2 text-sm text-text-primary outline-none ring-1 ring-accent-primary"
                  />
                ) : (
                  <button
                    onClick={() => navigate(`/chat/${c.id}`)}
                    className="flex min-w-0 flex-1 flex-col items-start gap-0.5 px-2.5 py-2 text-left"
                  >
                    <span className="w-full truncate text-sm text-text-primary">
                      {c.title || "Untitled"}
                    </span>
                    <span className="flex items-center gap-1.5 text-[0.7rem] text-text-muted">
                      <span>{formatRelativeTime(c.created_at)}</span>
                      {c.token_count > 0 && (
                        <span className="rounded bg-bg-elevated/60 px-1 font-mono">
                          {formatCompact(c.token_count)} tok
                        </span>
                      )}
                    </span>
                  </button>
                )}

                {!isEditing && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuId((m) => (m === c.id ? null : c.id));
                    }}
                    className={cn(
                      "mr-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-muted transition-opacity hover:bg-bg-elevated hover:text-text-primary",
                      menuId === c.id ? "opacity-100" : "opacity-0 group-hover:opacity-100"
                    )}
                    aria-label="Conversation options"
                  >
                    <MoreVertical size={15} />
                  </button>
                )}

                {menuId === c.id && (
                  <>
                  {/* Backdrop — swallows clicks so underlying chats can't be selected */}
                  <div
                    className="fixed inset-0 z-40"
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuId(null);
                    }}
                  />
                  <div
                    className="absolute right-1 top-[calc(100%-4px)] z-50 w-36 overflow-hidden rounded-lg border border-border bg-bg-secondary p-1 shadow-[0_12px_40px_-12px_rgba(0,0,0,0.7)] animate-fade-in"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      onClick={() => startRename(c.id, c.title)}
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-text-secondary hover:bg-bg-tertiary hover:text-text-primary"
                    >
                      <Pencil size={14} /> Rename
                    </button>
                    <button
                      onClick={() => {
                        setMenuId(null);
                        setDeleteId(c.id);
                      }}
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-danger hover:bg-danger/10"
                    >
                      <Trash2 size={14} /> Delete
                    </button>
                  </div>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <ConfirmDialog
        open={deleteId !== null}
        title="Delete conversation"
        message="Are you sure? This conversation and its messages will be permanently removed. This cannot be undone."
        confirmLabel="Delete"
        isLoading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteId(null)}
      />
    </>
  );
}
