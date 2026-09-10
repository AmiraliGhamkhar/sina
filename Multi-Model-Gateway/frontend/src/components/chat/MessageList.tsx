import { useEffect, useRef, useState } from "react";
import { Sparkles, AlertTriangle, RotateCw } from "lucide-react";
import type { Message } from "@/lib/types";
import { MessageBubble } from "./MessageBubble";
import { Skeleton } from "@/components/ui/Skeleton";

interface MessageListProps {
  messages: Message[];
  loading: boolean;
  pendingUserContent: string | null;
  streamedContent: string;
  isStreaming: boolean;
  streamError: string | null;
  onRetry: () => void;
  onRegenerate?: () => void;
  /** Scroll to + flash this message once it's loaded (branch "jump to fork"). */
  jumpToId?: string | null;
}

export function MessageList({
  messages,
  loading,
  pendingUserContent,
  streamedContent,
  isStreaming,
  streamError,
  onRetry,
  onRegenerate,
  jumpToId,
}: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const prevPendingRef = useRef<string | null>(null);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    // "stuck to bottom" if within 80px of the end
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  useEffect(() => {
    // Re-engage following only on a *new* user turn — NOT on every streamed
    // token (pendingUserContent stays set for the whole stream). Otherwise
    // honor the user's scroll position so they can scroll up mid-stream.
    const justSent = !!pendingUserContent && !prevPendingRef.current;
    prevPendingRef.current = pendingUserContent;
    if (justSent) stickRef.current = true;
    if (stickRef.current) bottomRef.current?.scrollIntoView({ behavior: "auto" });
  }, [messages.length, streamedContent, pendingUserContent, isStreaming]);

  // Branch lineage jump: once the target message is rendered, center it and
  // flash it. One-shot per jump target; declared after the stick effect so its
  // scroll wins, and it un-sticks the list so bottom-follow doesn't fight it.
  const [flashId, setFlashId] = useState<string | null>(null);
  const jumpedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!jumpToId || loading || jumpedRef.current === jumpToId) return;
    if (!messages.some((m) => m.id === jumpToId)) return;
    jumpedRef.current = jumpToId;
    stickRef.current = false;
    requestAnimationFrame(() => {
      document.getElementById(`msg-${jumpToId}`)?.scrollIntoView({ block: "center" });
      setFlashId(jumpToId);
    });
  }, [jumpToId, loading, messages]);

  if (loading && messages.length === 0) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
        <Skeleton className="ml-auto h-14 w-1/2" />
        <Skeleton className="h-24 w-3/4" />
        <Skeleton className="ml-auto h-14 w-2/5" />
      </div>
    );
  }

  const isEmpty =
    messages.length === 0 && !pendingUserContent && !isStreaming;

  if (isEmpty) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center animate-fade-in">
        <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-bg-tertiary">
          <Sparkles size={26} className="text-accent-primary" />
        </span>
        <h2 className="text-2xl text-text-primary">Welcome to llm-gateway</h2>
        <p className="max-w-md text-sm text-text-secondary">
          Start by selecting a preset and provider, then type your message below.
        </p>
      </div>
    );
  }

  const lastAssistantId = [...messages].reverse().find((m) => m.role === "assistant")?.id;

  return (
    <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
        {messages.map((m) => (
          <div key={m.id} id={`msg-${m.id}`} className={m.id === flashId ? "gw-flash" : undefined}>
            <MessageBubble
              role={m.role}
              content={m.content}
              message={m}
              onRegenerate={
                m.role === "assistant" && m.id === lastAssistantId ? onRegenerate : undefined
              }
            />
          </div>
        ))}

        {/* Optimistic in-flight turn */}
        {pendingUserContent && (
          <MessageBubble role="user" content={pendingUserContent} />
        )}
        {isStreaming && (
          <MessageBubble role="assistant" content={streamedContent} streaming />
        )}

        {streamError && (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 animate-fade-in">
            <span className="flex items-center gap-2 text-sm text-danger">
              <AlertTriangle size={16} />
              {streamError}
            </span>
            <button
              onClick={onRetry}
              className="flex items-center gap-1.5 rounded-md border border-danger/40 px-2.5 py-1 text-[0.8125rem] text-danger hover:bg-danger/10"
            >
              <RotateCw size={13} /> Retry
            </button>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}
