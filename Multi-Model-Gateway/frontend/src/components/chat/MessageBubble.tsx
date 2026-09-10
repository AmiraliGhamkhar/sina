import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Check, Copy, RotateCw, Pencil, GitBranch, Trash2 } from "lucide-react";
import type { Message } from "@/lib/types";
import { useChatStore } from "@/stores/chat-store";
import { toast } from "@/stores/ui-store";
import { cn, formatRelativeTime, formatCompact, getProviderInfo } from "@/lib/utils";
import { Markdown } from "./Markdown";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

interface MessageBubbleProps {
  role: "user" | "assistant" | "system";
  content: string;
  message?: Message;
  /** Show the pulsing streaming cursor at the end of assistant content. */
  streaming?: boolean;
  /** Provided only for the last assistant message — re-runs the last turn. */
  onRegenerate?: () => void;
}

function ActionButton({
  icon,
  label,
  onClick,
  danger,
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "flex h-6 w-6 items-center justify-center rounded-md text-text-muted opacity-0 transition-opacity hover:bg-bg-elevated group-hover:opacity-100",
        danger ? "hover:text-danger" : "hover:text-text-primary"
      )}
    >
      {icon}
    </button>
  );
}

export function MessageBubble({ role, content, message, streaming, onRegenerate }: MessageBubbleProps) {
  const navigate = useNavigate();
  const editMessage = useChatStore((s) => s.editMessage);
  const deleteMessage = useChatStore((s) => s.deleteMessage);
  const branchConversation = useChatStore((s) => s.branchConversation);

  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(content);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);

  // Actions only apply to a persisted message — not the optimistic/paused ones.
  const persisted = !!message && !message.id.startsWith("local-");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard may be unavailable over http */
    }
  };

  const saveEdit = async () => {
    if (!message) return;
    if (draft.trim() === content.trim()) {
      setEditing(false);
      return;
    }
    setBusy(true);
    try {
      await editMessage(message.id, draft);
      setEditing(false);
    } catch {
      toast.error("Could not edit message.");
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    if (!message) return;
    try {
      await deleteMessage(message.id);
      toast.success("Message deleted.");
    } catch {
      toast.error("Could not delete message.");
    } finally {
      setConfirmDelete(false);
    }
  };

  const branch = async () => {
    if (!message) return;
    setBusy(true);
    try {
      const id = await branchConversation(message.id);
      if (id) {
        toast.success("Branched to a new chat.");
        navigate(`/chat/${id}`);
      }
    } catch {
      toast.error("Could not branch.");
    } finally {
      setBusy(false);
    }
  };

  if (role === "user") {
    return (
      <div className="group flex flex-col items-end gap-1 animate-slide-up">
        <div className="max-w-[75%] rounded-2xl rounded-br-md bg-[#3A2D5E] px-4 py-2.5 text-text-primary">
          <p className="whitespace-pre-wrap break-words text-[0.9375rem] leading-relaxed">{content}</p>
        </div>
        {message && (
          <span className="px-1 text-[0.7rem] text-text-muted">
            {formatRelativeTime(message.created_at)}
          </span>
        )}
      </div>
    );
  }

  // assistant / system
  const provider = message?.model_used ? getProviderInfo(message.model_used) : null;

  return (
    <div className="group flex flex-col items-start gap-1 animate-slide-up">
      <div className="w-full max-w-[85%] rounded-2xl rounded-bl-md bg-bg-tertiary px-4 py-3 text-text-primary">
        {editing ? (
          <div className="flex flex-col gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              autoFocus
              className="min-h-[120px] w-full resize-y rounded-md bg-bg-primary px-3 py-2 text-[0.9375rem] text-text-primary outline-none ring-1 ring-accent-primary"
            />
            <div className="flex items-center gap-2">
              <button
                onClick={saveEdit}
                disabled={busy}
                className="rounded-md bg-accent-primary px-3 py-1 text-[0.8125rem] text-white transition-[filter] hover:brightness-110 disabled:opacity-50"
              >
                Save
              </button>
              <button
                onClick={() => {
                  setEditing(false);
                  setDraft(content);
                }}
                className="rounded-md px-3 py-1 text-[0.8125rem] text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : content ? (
          <Markdown content={content} />
        ) : streaming ? (
          <span className="inline-flex gap-1 py-1">
            <span className="h-2 w-2 animate-pulse-dot rounded-full bg-accent-primary" />
            <span
              className="h-2 w-2 animate-pulse-dot rounded-full bg-accent-primary"
              style={{ animationDelay: "0.2s" }}
            />
            <span
              className="h-2 w-2 animate-pulse-dot rounded-full bg-accent-primary"
              style={{ animationDelay: "0.4s" }}
            />
          </span>
        ) : null}
        {streaming && content && (
          <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse-dot rounded-full bg-accent-primary align-middle" />
        )}
      </div>

      <div className="flex items-center gap-2 px-1">
        {provider && (
          <span className="flex items-center gap-1.5 text-[0.7rem] text-text-muted">
            <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: provider.color }} />
            <span>{provider.provider}</span>
            <span className="font-mono">· {message?.model_used}</span>
          </span>
        )}
        {message?.tokens_used ? (
          <span className="text-[0.7rem] text-text-muted">{formatCompact(message.tokens_used)} tok</span>
        ) : null}

        {content && !streaming && !editing && (
          <div className="flex items-center gap-0.5">
            {onRegenerate && persisted && (
              <ActionButton icon={<RotateCw size={13} />} label="Regenerate" onClick={onRegenerate} />
            )}
            <ActionButton
              icon={copied ? <Check size={13} className="text-success" /> : <Copy size={13} />}
              label="Copy"
              onClick={copy}
            />
            {persisted && (
              <ActionButton
                icon={<Pencil size={13} />}
                label="Edit"
                onClick={() => {
                  setDraft(content);
                  setEditing(true);
                }}
              />
            )}
            {persisted && (
              <ActionButton icon={<GitBranch size={13} />} label="Branch out" onClick={branch} />
            )}
            {persisted && (
              <ActionButton
                icon={<Trash2 size={13} />}
                label="Delete"
                onClick={() => setConfirmDelete(true)}
                danger
              />
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete message"
        message="Delete this message? This cannot be undone."
        confirmLabel="Delete"
        onConfirm={doDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
