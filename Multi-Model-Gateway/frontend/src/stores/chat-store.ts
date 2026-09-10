import { create } from "zustand";
import { convoApi } from "@/lib/api-endpoints";
import type { Conversation, Message, Provider } from "@/lib/types";

function sortConversations(list: Conversation[]): Conversation[] {
  return [...list].sort(
    (a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );
}

// Backend does not order messages, and a user+assistant turn shares one `index`.
// Order by index, then put the user message before the assistant within a turn.
function sortMessages(list: Message[]): Message[] {
  const roleRank = (r: string) => (r === "system" ? 0 : r === "user" ? 1 : 2);
  return [...list].sort((a, b) => {
    const ai = a.index ?? 0;
    const bi = b.index ?? 0;
    if (ai !== bi) return ai - bi;
    const rr = roleRank(a.role) - roleRank(b.role);
    if (rr !== 0) return rr;
    return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
  });
}

interface ChatState {
  conversations: Conversation[];
  activeConversationId: string | null;
  messages: Message[];
  isStreaming: boolean;
  streamedContent: string;
  streamError: string | null;
  loadingConversations: boolean;
  loadingMessages: boolean;

  // Compose selections shared by header + input bar
  provider: Provider;
  model: string;
  presetId: string | null;
  isPrivate: boolean;

  fetchConversations: () => Promise<void>;
  createConversation: (title: string) => Promise<string>;
  setActiveConversation: (id: string | null) => void;
  startNewChat: () => void;
  fetchMessages: (id: string) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  editMessage: (id: string, content: string) => Promise<void>;
  deleteMessage: (id: string) => Promise<void>;
  branchConversation: (messageId: string) => Promise<string | null>;

  appendToStream: (content: string) => void;
  setStreaming: (streaming: boolean) => void;
  clearStream: () => void;
  setStreamError: (err: string | null) => void;
  appendPausedTurn: (userContent: string, assistantContent: string) => void;

  setProvider: (p: Provider) => void;
  setModelSelection: (provider: Provider, model: string) => void;
  setPresetId: (id: string | null) => void;
  setPrivate: (v: boolean) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  activeConversationId: null,
  messages: [],
  isStreaming: false,
  streamedContent: "",
  streamError: null,
  loadingConversations: false,
  loadingMessages: false,

  provider: "auto",
  model: "auto",
  presetId: null,
  isPrivate: false,

  fetchConversations: async () => {
    set({ loadingConversations: true });
    try {
      const convos = await convoApi.list();
      set({ conversations: sortConversations(convos) });
    } finally {
      set({ loadingConversations: false });
    }
  },

  createConversation: async (title) => {
    const { id } = await convoApi.create(title);
    set({ activeConversationId: id, messages: [], streamError: null });
    // Refresh list so the new conversation appears with its metadata.
    get().fetchConversations();
    return id;
  },

  setActiveConversation: (id) => {
    set({ activeConversationId: id, streamError: null });
    if (id) {
      get().fetchMessages(id);
    } else {
      set({ messages: [] });
    }
  },

  startNewChat: () => {
    set({ activeConversationId: null, messages: [], streamError: null, streamedContent: "" });
  },

  fetchMessages: async (id) => {
    set({ loadingMessages: true });
    try {
      const messages = await convoApi.getMessages(id);
      const sorted = sortMessages(messages);
      set({ messages: sorted });
      // Per-chat model memory: reuse the model this conversation last used, so
      // each chat keeps its own model until the user picks a different one.
      // (provider is derived the same way the rest of the app does — "/" = OpenRouter.)
      const lastModel = [...sorted]
        .reverse()
        .find((m) => m.role === "assistant" && m.model_used)?.model_used;
      if (lastModel) {
        set({ provider: lastModel.includes("/") ? "openrouter" : "local", model: lastModel });
      }
    } finally {
      set({ loadingMessages: false });
    }
  },

  renameConversation: async (id, title) => {
    await convoApi.rename(id, title);
    set((state) => ({
      conversations: state.conversations.map((c) =>
        c.id === id ? { ...c, title } : c
      ),
    }));
  },

  deleteConversation: async (id) => {
    await convoApi.delete(id);
    set((state) => {
      const wasActive = state.activeConversationId === id;
      return {
        conversations: state.conversations.filter((c) => c.id !== id),
        activeConversationId: wasActive ? null : state.activeConversationId,
        messages: wasActive ? [] : state.messages,
      };
    });
  },

  editMessage: async (id, content) => {
    const cid = get().activeConversationId;
    if (!cid) return;
    await convoApi.editMessage(cid, id, content);
    set((state) => ({
      messages: state.messages.map((m) => (m.id === id ? { ...m, content } : m)),
    }));
  },

  deleteMessage: async (id) => {
    const cid = get().activeConversationId;
    if (!cid) return;
    await convoApi.deleteMessage(cid, id);
    set((state) => ({ messages: state.messages.filter((m) => m.id !== id) }));
  },

  branchConversation: async (messageId) => {
    const cid = get().activeConversationId;
    if (!cid) return null;
    const { id } = await convoApi.branch(cid, messageId);
    await get().fetchConversations();
    return id;
  },

  appendToStream: (content) =>
    set((state) => ({ streamedContent: state.streamedContent + content })),

  setStreaming: (streaming) => set({ isStreaming: streaming }),

  clearStream: () => set({ streamedContent: "", streamError: null }),

  setStreamError: (err) => set({ streamError: err }),

  // Freeze a cancelled in-flight turn into the message list. The backend never
  // persists a cancelled turn, so this keeps the user's input + partial reply
  // visible instead of vanishing on cancel.
  appendPausedTurn: (userContent, assistantContent) =>
    set((state) => {
      const baseIndex = state.messages.reduce((max, m) => Math.max(max, m.index ?? 0), 0);
      const now = new Date().toISOString();
      const convoId = state.activeConversationId ?? "";
      const items: Message[] = [
        {
          id: `local-user-${Date.now()}`,
          conversation_id: convoId,
          role: "user",
          content: userContent,
          created_at: now,
          model_used: null,
          tokens_used: null,
          index: baseIndex + 1,
        },
      ];
      if (assistantContent) {
        items.push({
          id: `local-asst-${Date.now()}`,
          conversation_id: convoId,
          role: "assistant",
          content: assistantContent,
          created_at: now,
          model_used: null,
          tokens_used: null,
          index: baseIndex + 2,
        });
      }
      return { messages: [...state.messages, ...items] };
    }),

  setProvider: (p) => set({ provider: p }),
  setModelSelection: (provider, model) =>
    // Picking a local model defaults privacy on (keeps inference off the cloud).
    set(provider === "local" ? { provider, model, isPrivate: true } : { provider, model }),
  setPresetId: (id) => set({ presetId: id }),
  setPrivate: (v) => set({ isPrivate: v }),
}));
