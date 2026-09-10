import { useCallback, useEffect } from "react";
import { useLocation, useParams } from "react-router-dom";
import { useChatStore } from "@/stores/chat-store";
import { useChat } from "@/hooks/use-chat";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { MessageList } from "@/components/chat/MessageList";
import { ChatInput } from "@/components/chat/ChatInput";

export default function ChatPage() {
  const { id: routeId } = useParams();
  const location = useLocation();
  // Set when arriving via a branch's "from …" chip — the message to scroll to.
  const jumpToId = (location.state as { jumpTo?: string | null } | null)?.jumpTo ?? null;

  const messages = useChatStore((s) => s.messages);
  const loadingMessages = useChatStore((s) => s.loadingMessages);

  const { send, cancelStream, retryLast, isStreaming, streamedContent, streamError, pendingUserContent } =
    useChat();

  // The URL is the single source of truth for the active conversation.
  // Auto-created conversations are pushed to the URL from useChat.send(), so
  // there's no store→URL effect — that two-way sync was the loop-prone part.
  // The `!== routeId` guard avoids re-fetching the conversation we're already
  // on (which would wipe an in-flight stream right after auto-creation).
  useEffect(() => {
    const store = useChatStore.getState();
    if (routeId) {
      if (store.activeConversationId !== routeId) store.setActiveConversation(routeId);
    } else if (store.activeConversationId !== null) {
      store.startNewChat();
    }
  }, [routeId]);

  // Regenerate the last reply: drop the last turn (user + assistant), then
  // re-send the same prompt so a fresh response streams in its place.
  const handleRegenerate = useCallback(async () => {
    const store = useChatStore.getState();
    const msgs = store.messages;
    const lastAssistant = [...msgs].reverse().find((m) => m.role === "assistant");
    if (!lastAssistant) return;
    const pairedUser =
      msgs.find((m) => m.role === "user" && m.index === lastAssistant.index) ??
      [...msgs].reverse().find((m) => m.role === "user");
    if (!pairedUser?.content) return;
    try {
      await store.deleteMessage(lastAssistant.id);
      await store.deleteMessage(pairedUser.id);
    } catch {
      /* ignore — the re-send still proceeds */
    }
    void send(pairedUser.content);
  }, [send]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ChatHeader />
      <div className="min-h-0 flex-1">
        <MessageList
          messages={messages}
          loading={loadingMessages}
          pendingUserContent={pendingUserContent}
          streamedContent={streamedContent}
          isStreaming={isStreaming}
          streamError={streamError}
          onRetry={retryLast}
          onRegenerate={handleRegenerate}
          jumpToId={jumpToId}
        />
      </div>
      <ChatInput onSend={(c) => send(c)} onCancel={cancelStream} isStreaming={isStreaming} />
    </div>
  );
}
