import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useChatStore } from "@/stores/chat-store";
import { chatApi } from "@/lib/api-endpoints";
import type { ChatRequest, Provider } from "@/lib/types";

interface SendOptions {
  presetId?: string | null;
  provider?: Provider;
  private?: boolean;
}

function deriveTitle(content: string): string {
  const words = content.trim().split(/\s+/).slice(0, 6).join(" ");
  return words.length > 0 ? words : "New Chat";
}

export function useChat() {
  const navigate = useNavigate();
  const abortRef = useRef<AbortController | null>(null);
  const lastAttemptRef = useRef<{ content: string; options?: SendOptions } | null>(null);
  const [pendingUserContent, setPendingUserContent] = useState<string | null>(null);

  const isStreaming = useChatStore((s) => s.isStreaming);
  const streamedContent = useChatStore((s) => s.streamedContent);
  const streamError = useChatStore((s) => s.streamError);

  const send = useCallback(async (content: string, options?: SendOptions) => {
    const store = useChatStore.getState();
    if (!content.trim() || store.isStreaming) return;

    lastAttemptRef.current = { content, options };

    let conversationId = store.activeConversationId;
    const wasNew = !conversationId;

    try {
      if (!conversationId) {
        conversationId = await store.createConversation("New Chat");
        // Reflect the new conversation in the URL (replaces the old store→URL effect).
        navigate(`/chat/${conversationId}`, { replace: true });
      }
    } catch {
      store.setStreamError("Could not start a conversation. Is the backend running?");
      return;
    }

    const abortController = new AbortController();
    abortRef.current = abortController;

    store.setStreamError(null);
    store.clearStream();
    store.setStreaming(true);
    setPendingUserContent(content);

    const request: ChatRequest = {
      conversation_id: conversationId,
      preset_id: options?.presetId !== undefined ? options.presetId : store.presetId,
      messages: [{ role: "user", content }],
      // "auto" lets the backend route by provider; a specific id pins the model.
      model: store.model,
      stream: true,
      provider: options?.provider ?? store.provider,
      private: options?.private ?? store.isPrivate,
    };

    await chatApi.stream(
      request,
      (token) => useChatStore.getState().appendToStream(token),
      async () => {
        abortRef.current = null;
        const s = useChatStore.getState();
        try {
          if (wasNew) {
            await s.renameConversation(conversationId!, deriveTitle(content));
          }
          await s.fetchMessages(conversationId!);
          await s.fetchConversations();
        } catch {
          /* non-fatal: streamed content already shown */
        } finally {
          s.setStreaming(false);
          s.clearStream();
          setPendingUserContent(null);
        }
      },
      (error) => {
        abortRef.current = null;
        const s = useChatStore.getState();
        s.setStreaming(false);
        s.setStreamError(error);
        s.clearStream();
        setPendingUserContent(null);
        // Re-sync in case the backend persisted a partial turn.
        if (conversationId) s.fetchMessages(conversationId);
      },
      abortController.signal
    );
  }, [navigate]);

  const cancelStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    const s = useChatStore.getState();
    const userContent = pendingUserContent;
    const partial = s.streamedContent;
    s.setStreaming(false);
    // Freeze the in-flight turn so the user's input + partial reply stay put.
    // The backend doesn't persist a cancelled turn, so the old refetch wiped it
    // (reverting to the welcome screen or dropping the prompt) — keep it instead.
    if (userContent) s.appendPausedTurn(userContent, partial);
    s.clearStream();
    setPendingUserContent(null);
  }, [pendingUserContent]);

  const retryLast = useCallback(() => {
    const last = lastAttemptRef.current;
    useChatStore.getState().setStreamError(null);
    if (last) void send(last.content, last.options);
  }, [send]);

  return {
    send,
    cancelStream,
    retryLast,
    isStreaming,
    streamedContent,
    streamError,
    pendingUserContent,
  };
}
