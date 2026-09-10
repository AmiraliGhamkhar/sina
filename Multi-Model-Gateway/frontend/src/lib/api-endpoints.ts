import { apiClient } from "./api-client";
import type {
  LoginResponse,
  RefreshResponse,
  Conversation,
  ConvoCreateResponse,
  Message,
  Preset,
  PresetCreate,
  PresetUpdate,
  ProviderRow,
  ProviderCreate,
  ProviderUpdate,
  ProviderTestResult,
  PromptTemplate,
  TemplateCreate,
  TemplateUpdate,
  Workflow,
  WorkflowCreate,
  WorkflowUpdate,
  RewriteResponse,
  ImageGenerateRequest,
  ImageGenerateResponse,
  ImageStatusResponse,
  AspectRatiosResponse,
  ListEnvelope,
  LocalModel,
  OpenRouterListResponse,
  ChatRequest,
  AgentToolInfo,
  AgentEvent,
  ResearchJob,
  ResearchJobDetail,
  ResearchCreateResponse,
  ResearchEvent,
  HardwareInfo,
  CookbookResponse,
  HfCookbookResponse,
  HfModelDetail,
} from "./types";

/**
 * Build a query string from a params object, dropping null/undefined entries.
 * The only place list/query params are serialized, so shapes stay consistent.
 */
function buildQuery(
  params: Record<string, string | number | boolean | null | undefined>
): string {
  const entries = Object.entries(params)
    .filter(([, v]) => v != null)
    .map(([k, v]) => [k, String(v)] as [string, string]);
  const qs = new URLSearchParams(entries).toString();
  return qs ? `?${qs}` : "";
}

// ───────────── Chat ─────────────
// Streamed via the transport's SSE reader (plain text tokens, [DONE]/[ERROR]).
export const chatApi = {
  stream: (
    body: ChatRequest,
    onToken: (token: string) => void,
    onDone: () => void,
    onError: (error: string) => void,
    signal?: AbortSignal
  ): Promise<void> =>
    apiClient.streamChat(body, onToken, onDone, onError, signal),
};

// ───────────── Auth ─────────────
export const authApi = {
  register: (email: string, password: string) =>
    apiClient.request<{ message: string }>("POST", "/auth/register", { email, password }),

  login: (email: string, password: string, deviceId?: string) =>
    apiClient.request<LoginResponse>("POST", "/auth/login", {
      email,
      password,
      ...(deviceId ? { device_id: deviceId } : {}),
    }),

  refresh: (refreshToken: string, deviceId?: string) =>
    apiClient.request<RefreshResponse>("POST", "/auth/refresh", {
      refresh_token: refreshToken,
      ...(deviceId ? { device_id: deviceId } : {}),
    }),

  logout: (refreshToken: string) =>
    apiClient.request<{ message: string }>("POST", "/auth/logout", {
      refresh_token: refreshToken,
    }),
};

// ───────────── Conversations (bare arrays) ─────────────
export const convoApi = {
  list: () => apiClient.request<Conversation[]>("GET", "/v1/convo"),

  create: (title: string) =>
    apiClient.request<ConvoCreateResponse>("POST", "/v1/convo", { title }),

  getMessages: (id: string) =>
    apiClient.request<Message[]>("GET", `/v1/convo/${id}`),

  rename: (id: string, title: string) =>
    apiClient.request<{ message: string }>("PATCH", `/v1/convo/${id}`, { title }),

  delete: (id: string) =>
    apiClient.request<{ message: string }>("DELETE", `/v1/convo/${id}`),

  editMessage: (convoId: string, messageId: string, content: string) =>
    apiClient.request<{ message: string }>(
      "PATCH",
      `/v1/convo/${convoId}/messages/${messageId}`,
      { content }
    ),

  deleteMessage: (convoId: string, messageId: string) =>
    apiClient.request<{ message: string }>(
      "DELETE",
      `/v1/convo/${convoId}/messages/${messageId}`
    ),

  branch: (convoId: string, messageId: string) =>
    apiClient.request<{ id: string }>("POST", `/v1/convo/${convoId}/branch`, {
      message_id: messageId,
    }),
};

// ───────────── Presets ({ data: [...] }) ─────────────
export const presetApi = {
  list: () => apiClient.request<ListEnvelope<Preset>>("GET", "/v1/presets"),

  get: (id: string) => apiClient.request<Preset>("GET", `/v1/presets/${id}`),

  create: (data: PresetCreate) =>
    apiClient.request<Preset>("POST", "/v1/presets", data),

  update: (id: string, data: PresetUpdate) =>
    apiClient.request<Preset>("PATCH", `/v1/presets/${id}`, data),

  delete: (id: string) =>
    apiClient.request<{ detail: string }>("DELETE", `/v1/presets/${id}`),
};

// ───────────── Providers ({ data: [...] }) ─────────────
export const providerApi = {
  list: () => apiClient.request<ListEnvelope<ProviderRow>>("GET", "/v1/providers"),

  create: (data: ProviderCreate) =>
    apiClient.request<ProviderRow>("POST", "/v1/providers", data),

  update: (id: string, data: ProviderUpdate) =>
    apiClient.request<ProviderRow>("PATCH", `/v1/providers/${id}`, data),

  delete: (id: string) =>
    apiClient.request<{ detail: string }>("DELETE", `/v1/providers/${id}`),

  test: (id: string) =>
    apiClient.request<ProviderTestResult>("POST", `/v1/providers/${id}/test`),
};

// ───────────── Templates ({ data: [...] }) ─────────────
export const templateApi = {
  list: () => apiClient.request<ListEnvelope<PromptTemplate>>("GET", "/v1/templates"),

  get: (id: string) =>
    apiClient.request<PromptTemplate>("GET", `/v1/templates/${id}`),

  create: (data: TemplateCreate) =>
    apiClient.request<PromptTemplate>("POST", "/v1/templates", data),

  update: (id: string, data: TemplateUpdate) =>
    apiClient.request<PromptTemplate>("PATCH", `/v1/templates/${id}`, data),

  delete: (id: string) =>
    apiClient.request<{ detail: string }>("DELETE", `/v1/templates/${id}`),

  rewrite: (prompt: string, templateId?: string | null) =>
    apiClient.request<RewriteResponse>("POST", "/v1/templates/rewrite", {
      prompt,
      template_id: templateId ?? null,
    }),
};

// ───────────── Workflows ({ data: [...] }) ─────────────
export const workflowApi = {
  list: () => apiClient.request<ListEnvelope<Workflow>>("GET", "/v1/workflows"),

  get: (id: string) => apiClient.request<Workflow>("GET", `/v1/workflows/${id}`),

  create: (data: WorkflowCreate) =>
    apiClient.request<Workflow>("POST", "/v1/workflows", data),

  update: (id: string, data: WorkflowUpdate) =>
    apiClient.request<Workflow>("PATCH", `/v1/workflows/${id}`, data),

  delete: (id: string) =>
    apiClient.request<{ detail: string }>("DELETE", `/v1/workflows/${id}`),
};

// ───────────── Images ─────────────
export const imageApi = {
  generate: (data: ImageGenerateRequest) =>
    apiClient.request<ImageGenerateResponse>("POST", "/v1/images/generate", data),

  status: (promptId: string) =>
    apiClient.request<ImageStatusResponse>("GET", `/v1/images/status/${promptId}`),

  aspectRatios: () =>
    apiClient.request<AspectRatiosResponse>("GET", "/v1/images/aspect-ratios"),

  // Authed binary fetch the transport exposes for `<img>`-unreachable URLs
  // (see lib/authed-image.ts). Generic enough to double as a file download.
  fetchBlob: (path: string, signal?: AbortSignal) =>
    apiClient.fetchBlob(path, signal),
};

// ───────────── Models ─────────────
export const modelApi = {
  listLocal: () => apiClient.request<ListEnvelope<LocalModel>>("GET", "/v1/models"),

  listOpenRouter: () =>
    apiClient.request<OpenRouterListResponse>("GET", "/v1/openrouter/models"),

  health: () => apiClient.request<{ status: string }>("GET", "/health"),
};

// ───────────── Agents / MCP ─────────────
export const agentApi = {
  tools: () => apiClient.request<ListEnvelope<AgentToolInfo>>("GET", "/v1/agent/tools"),

  setPermission: (name: string, allowed: boolean) =>
    apiClient.request<{ message: string }>(
      "PUT",
      `/v1/agent/tools/${encodeURIComponent(name)}/permission`,
      { allowed }
    ),

  // SSE: tool_call / tool_result / token / error / done (JSON per `data:` line).
  chatStream: (body: ChatRequest, signal?: AbortSignal) =>
    apiClient.streamEvents<AgentEvent>("POST", "/v1/agent/chat", body, signal),
};

// ───────────── User-created Agents ────────────
import type { Agent, AgentCreate, AgentUpdate } from "./types";
import type { AgentInstall, FileEdit } from "./types";
export const agentsApi = {
  list: (params?: { limit?: number; offset?: number }) =>
    apiClient.request<ListEnvelope<Agent>>(
      "GET",
      `/v1/agents${buildQuery(params ?? {})}`
    ),
  get: (id: string) => apiClient.request<Agent>("GET", `/v1/agents/${encodeURIComponent(id)}`),
  create: (data: AgentCreate) => apiClient.request<Agent>("POST", "/v1/agents", data),
  update: (id: string, data: AgentUpdate) => apiClient.request<Agent>("PATCH", `/v1/agents/${encodeURIComponent(id)}`, data),
  delete: (id: string) => apiClient.request<{ detail: string }>("DELETE", `/v1/agents/${encodeURIComponent(id)}`),
  suggest: (goal: string, description?: string) =>
    apiClient.request<{ name: string; description: string; system_prompt: string; suggested_tools: string[]; suggested_model: string | null }>(
      "POST",
      "/v1/agents/suggest",
      { goal, ...(description ? { description } : {}) }
    ),
};

// ───────────── Marketplace (public agents) ────────────
// Backed by GET /v1/marketplace/agents and POST /v1/agents/{id}/install in T2.
export const marketplaceApi = {
  list: (params?: { limit?: number; offset?: number }) =>
    apiClient.request<ListEnvelope<Agent>>(
      "GET",
      `/v1/marketplace/agents${buildQuery(params ?? {})}`
    ),
  myInstalls: () => apiClient.request<ListEnvelope<AgentInstall>>("GET", "/v1/agents/installs"),
  install: (agentId: string) =>
    apiClient.request<AgentInstall>("POST", `/v1/agents/${encodeURIComponent(agentId)}/install`, {}),
  uninstall: (agentId: string) =>
    apiClient.request<{ detail: string }>("DELETE", `/v1/agents/${encodeURIComponent(agentId)}/install`),
};

// ───────────── Workspace (per-user-per-agent files + undo) ────────────
export const workspaceApi = {
  files: (agentId: string, path = ".") =>
    apiClient.request<{ files: string[] }>("GET", `/v1/agents/${encodeURIComponent(agentId)}/workspace/files?path=${encodeURIComponent(path)}`),
  file: (agentId: string, path: string) =>
    apiClient.request<{ content: string; lines: { n: number; hash: string; text: string }[] }>(
      "GET",
      `/v1/agents/${encodeURIComponent(agentId)}/workspace/file?path=${encodeURIComponent(path)}`
    ),
  edits: (agentId: string, params?: { limit?: number; offset?: number }) =>
    apiClient.request<ListEnvelope<FileEdit>>(
      "GET",
      `/v1/agents/${encodeURIComponent(agentId)}/workspace/edits${buildQuery(params ?? {})}`
    ),
  undo: (agentId: string, editId: string) =>
    apiClient.request<{ edit_id: string; undone: string; commit_sha: string | null }>(
      "POST",
      `/v1/agents/${encodeURIComponent(agentId)}/workspace/undo`,
      { edit_id: editId }
    ),
};

// ───────────── Deep Research ─────────────
export const researchApi = {
  create: (query: string, provider?: string, model?: string) =>
    apiClient.request<ResearchCreateResponse>("POST", "/v1/research", {
      query,
      provider,
      model,
    }),

  list: () => apiClient.request<ListEnvelope<ResearchJob>>("GET", "/v1/research"),

  get: (id: string) => apiClient.request<ResearchJobDetail>("GET", `/v1/research/${id}`),

  cancel: (id: string) =>
    apiClient.request<{ message: string }>("POST", `/v1/research/${id}/cancel`),

  // SSE: progress / done / error (JSON per `data:` line); snapshot-on-connect.
  stream: (id: string, signal?: AbortSignal) =>
    apiClient.streamEvents<ResearchEvent>("GET", `/v1/research/${id}/stream`, undefined, signal),
};

// ───────────── Cookbook / Hardware ─────────────
export const hardwareApi = {
  hardware: () => apiClient.request<HardwareInfo>("GET", "/v1/hardware"),

  cookbook: (contextTokens: number) =>
    apiClient.request<CookbookResponse>(
      "GET",
      `/v1/cookbook?context_tokens=${contextTokens}`
    ),
};

// ───────────── Hugging Face cookbook ─────────────
export const hfApi = {
  models: (search: string, limit: number, contextTokens: number) =>
    apiClient.request<HfCookbookResponse>(
      "GET",
      `/v1/hf/models?${new URLSearchParams({
        search,
        limit: String(limit),
        context_tokens: String(contextTokens),
      })}`
    ),

  detail: (repoId: string, contextTokens: number) =>
    apiClient.request<HfModelDetail>(
      "GET",
      `/v1/hf/models/${encodeURIComponent(repoId)}?context_tokens=${contextTokens}`
    ),
};
