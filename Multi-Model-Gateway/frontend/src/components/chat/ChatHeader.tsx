import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Lock, GitBranch } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { usePresetStore } from "@/stores/preset-store";
import { toast } from "@/stores/ui-store";
import { formatCompact, truncate } from "@/lib/utils";

const PROVIDER_LABEL: Record<string, string> = {
  auto: "Auto",
  local: "Local",
  openrouter: "OpenRouter",
};

// Rectangular chip — matches the token style used in the conversation sidebar.
const CHIP = "shrink-0 rounded bg-bg-elevated/60 px-1.5 py-0.5 text-[0.7rem] text-text-secondary";

/** "openai/gpt-4o" → "gpt-4o" for compact display. */
function short(id: string): string {
  const i = id.lastIndexOf("/");
  return i >= 0 ? id.slice(i + 1) : id;
}

export function ChatHeader() {
  const navigate = useNavigate();
  const activeId = useChatStore((s) => s.activeConversationId);
  const conversations = useChatStore((s) => s.conversations);
  const provider = useChatStore((s) => s.provider);
  const model = useChatStore((s) => s.model);
  const presetId = useChatStore((s) => s.presetId);
  const isPrivate = useChatStore((s) => s.isPrivate);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const presets = usePresetStore((s) => s.presets);

  const convo = conversations.find((c) => c.id === activeId);
  const preset = presets.find((p) => p.id === presetId);

  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const commit = async () => {
    setEditing(false);
    const title = value.trim();
    if (!convo || !title || title === convo.title) return;
    try {
      await renameConversation(convo.id, title);
    } catch {
      toast.error("Could not rename conversation.");
    }
  };

  // Show the actual model once one is pinned; otherwise the provider label.
  const modelLabel = model !== "auto" ? short(model) : PROVIDER_LABEL[provider];

  return (
    <header className="flex items-center justify-between gap-4 border-b border-border bg-bg-secondary/60 px-5 py-3 max-md:pl-14 max-md:pr-14">
      {/* Left — title, then model + tokens for this chat */}
      <div className="flex min-w-0 items-center gap-2">
        {editing && convo ? (
          <input
            ref={inputRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") setEditing(false);
            }}
            className="w-full max-w-md rounded-md bg-bg-primary px-2 py-1 text-base text-text-primary outline-none ring-1 ring-accent-primary"
          />
        ) : (
          <button
            onClick={() => {
              if (!convo) return;
              setValue(convo.title);
              setEditing(true);
            }}
            disabled={!convo}
            className="min-w-0 max-w-md truncate text-left text-base text-text-primary hover:text-accent-primary transition-colors disabled:hover:text-text-primary"
            title={convo ? "Click to rename" : undefined}
          >
            {convo ? convo.title || "Untitled" : "New chat"}
          </button>
        )}

        <span className={`${CHIP} font-mono`} title={model !== "auto" ? model : undefined}>
          {modelLabel}
        </span>
        {convo && convo.token_count > 0 && (
          <span className={`${CHIP} font-mono`}>{formatCompact(convo.token_count)} tok</span>
        )}
        {convo?.parent_id && (
          <button
            onClick={() =>
              navigate(`/chat/${convo.parent_id}`, {
                state: { jumpTo: convo.branched_from_message_id },
              })
            }
            className={`${CHIP} flex items-center gap-1 transition-colors hover:text-accent-primary`}
            title="Jump to where this chat branched from"
          >
            <GitBranch size={11} className="text-accent-secondary" />
            from{" "}
            {truncate(
              conversations.find((c) => c.id === convo.parent_id)?.title ?? "original chat",
              24
            )}
          </button>
        )}
      </div>

      {/* Right — preset + privacy */}
      <div className="flex shrink-0 items-center gap-2">
        {preset && <span className={CHIP}>{preset.name}</span>}
        {isPrivate && (
          <span className={`${CHIP} flex items-center gap-1`}>
            <Lock size={11} className="text-accent-secondary" />
            Private
          </span>
        )}
      </div>
    </header>
  );
}
