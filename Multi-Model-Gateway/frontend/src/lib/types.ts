// ============================================================
// Types mirror the FastAPI backend (the source of truth).
// Verified against backend/app/routers/* and backend/app/models/*.
// ============================================================

// ───────────── Auth ─────────────
export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface RefreshResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export type Provider = "auto" | "local" | "openrouter";

// ───────────── Chat ─────────────
export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface ChatRequest {
  conversation_id: string | null;
  preset_id: string | null;
  messages: ChatMessage[];
  // The backend only performs "auto" routing when model === "auto";
  // otherwise it pins the given model. Default to "auto".
  model?: string;
  stream?: boolean;
  provider?: Provider;
  private?: boolean;
  // Optional agent binding — when set the backend runs as that agent
  // (agent's system_prompt + filtered allowed_tools win; ADR-0004).
  agent_id?: string | null;
  agent_version?: number | null;
  // Pin a specific provider row; overrides routing heuristics
  provider_id?: string | null;
}

// ───────────── Conversations ─────────────
export interface Conversation {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
  token_count: number;
  // Branch lineage: set when this conversation was forked from another.
  parent_id: string | null;
  branched_from_message_id: string | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  model_used: string | null;
  tokens_used: number | null;
  index: number | null;
}

// POST /v1/convo → { message, id }
export interface ConvoCreateResponse {
  message: string;
  id: string;
}

// ───────────── Presets ─────────────
export interface Preset {
  id: string;
  user_id: string;
  name: string;
  system_prompt: string | null;
  temperature: number;
  token_limit: number | null;
  context_overflow: string | null;
  stop_strings: string[] | null;
  top_k: number | null;
  top_p: number | null;
  min_p: number | null;
  repeat_penalty: number | null;
  created_at: string;
}

export interface PresetCreate {
  name: string;
  system_prompt?: string | null;
  temperature?: number;
  token_limit?: number | null;
  context_overflow?: string | null;
  stop_strings?: string[] | null;
  top_k?: number | null;
  top_p?: number | null;
  min_p?: number | null;
  repeat_penalty?: number | null;
}

export type PresetUpdate = Partial<PresetCreate>;

// ───────────── Providers (BYO-key) ─────────────
export type ProviderType =
  | "openai_compatible"
  | "openai"
  | "anthropic"
  | "google"
  | "openrouter";

export type ProviderRole = "local" | "cloud";

export interface ProviderRow {
  id: string;
  name: string;
  type: ProviderType;
  role: ProviderRole;
  base_url?: string | null;
  default_model?: string | null;
  is_default: boolean;
  enabled: boolean;
  created_at: string;
  api_key_masked: string;
}

export interface ProviderCreate {
  name: string;
  type: ProviderType;
  role: ProviderRole;
  base_url?: string | null;
  api_key?: string | null;
  default_model?: string | null;
  is_default?: boolean;
  enabled?: boolean;
}

export interface ProviderUpdate {
  name?: string;
  type?: ProviderType;
  role?: ProviderRole;
  base_url?: string | null;
  api_key?: string | null;
  default_model?: string | null;
  is_default?: boolean;
  enabled?: boolean;
}

export interface ProviderTestResult {
  ok: boolean;
  model?: string | null;
  error?: string | null;
}

// ───────────── Templates ─────────────
export interface PromptTemplate {
  id: string;
  user_id: string;
  name: string;
  description: string;
  structure: string;
  created_at: string;
}

export interface TemplateCreate {
  name: string;
  description: string;
  structure: string;
}

export type TemplateUpdate = Partial<TemplateCreate>;

export interface Workflow {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  graph: Record<string, unknown>;
  param_map: Record<string, unknown> | null;
  created_at: string;
}

export interface WorkflowCreate {
  name: string;
  description?: string | null;
  graph: Record<string, unknown>;
  param_map?: Record<string, unknown> | null;
}

export type WorkflowUpdate = Partial<WorkflowCreate>;

export interface RewriteRequest {
  prompt: string;
  template_id?: string | null;
}

export interface RewriteResponse {
  rewritten_prompt: string;
}

// ───────────── Images ─────────────
export interface ImageGenerateRequest {
  prompt: string;
  negative_prompt?: string;
  template_id?: string | null;
  workflow_id?: string | null;
  steps?: number;
  cfg?: number;
  aspect_ratio?: string;
  batch_size?: number;
  seed?: number | null;
  rewrite?: boolean;
}

export interface ImageGenerateResponse {
  prompt_id: string;
  rewritten_prompt: string;
}

export interface ImageResult {
  filename: string;
  url: string;
}

export interface ImageStatusResponse {
  status: "pending" | "complete";
  images?: ImageResult[];
}

// GET /v1/images/aspect-ratios — backend is the single source of truth for the
// ResolutionSelector node's valid values.
export interface AspectRatiosResponse {
  aspect_ratios: string[];
  default: string;
}

// ───────────── Models ─────────────
export interface LocalModel {
  id: string;
  object: string;
}

export interface OpenRouterModel {
  id: string;
  name: string;
  context_length: number | null;
  description: string;
}

// ───────────── List envelopes ─────────────
// NOTE: presets & templates lists are wrapped in { data: [...] };
// conversations & messages are returned as BARE arrays.
export interface ListEnvelope<T> {
  data: T[];
}

export interface OpenRouterListResponse {
  data: OpenRouterModel[];
  count: number;
}

// ───────────── Agents / MCP ─────────────
export interface AgentToolInfo {
  name: string;
  description: string;
  first_party: boolean;
  allowed: boolean;
}

// ───────────── Agents ────────────
export interface Agent {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  system_prompt: string | null;
  preset_id: string | null;
  provider: string | null;
  model: string | null;
  allowed_tools: string[];
  max_iterations: number;
  token_budget: number;
  is_public: boolean;
  version: number;
  created_at: string;
}

export interface AgentCreate {
  name: string;
  description?: string | null;
  system_prompt?: string | null;
  preset_id?: string | null;
  provider?: string | null;
  model?: string | null;
  allowed_tools?: string[];
  max_iterations?: number;
  token_budget?: number;
  is_public?: boolean;
}

export type AgentUpdate = Partial<AgentCreate>;

export interface AgentInstall {
  id: string;
  user_id: string;
  agent_id: string;
  pinned_version: number;
  created_at: string;
}

export interface FileEdit {
  id: string;
  user_id: string;
  agent_id: string | null;
  store: string;
  path: string;
  patch: string;
  before_hash: string | null;
  after_hash: string | null;
  tool_call_id: string | null;
  created_at: string;
}

// SSE events from POST /v1/agent/chat (one JSON object per `data:` line).
// Re-exported from agent-events.ts, which owns the runtime validators
// (parseAgentEvent / parseFileEditResult) — the single source for the contract.
export type { AgentEvent } from "./agent-events";

// ───────────── Deep Research ─────────────
export type ResearchStatus =
  | "queued"
  | "running"
  | "complete"
  | "cancelled"
  | "error"
  | "failed";

export interface ResearchSource {
  n: number;
  title: string;
  url: string;
}

// List item (GET /v1/research)
export interface ResearchJob {
  id: string;
  query: string;
  status: ResearchStatus;
  stage: string | null;
  progress: number | null;
  created_at: string;
}

// Full job (GET /v1/research/{id})
export interface ResearchJobDetail extends ResearchJob {
  result: string | null;
  sources: ResearchSource[] | null;
  error: string | null;
}

export interface ResearchCreateResponse {
  job_id: string;
  status: ResearchStatus;
}

// SSE events from GET /v1/research/{id}/stream.
export type ResearchEvent =
  | { type: "progress"; stage: string; progress: number; message: string }
  | { type: "done"; status: "complete"; result: string; sources: ResearchSource[] }
  | { type: "done"; status: "cancelled" }
  | { type: "error"; message: string };

// ───────────── Cookbook / Hardware ─────────────
export interface GpuInfo {
  index: number;
  name: string;
  vram_total_mb: number;
  vram_free_mb: number;
}

export interface HardwareInfo {
  gpu_available: boolean;
  gpus: GpuInfo[];
  ram_total_mb: number | null;
}

export type CookbookVerdict =
  | "fits_fully"
  | "partial_offload"
  | "wont_fit"
  | "cpu_only"
  | "unknown";

export interface CookbookModel {
  id: string;
  source: string;
  params_b: number;
  quant: string;
  verdict: CookbookVerdict;
  score: number;
  need_gb: number;
  rationale: string;
}

export interface CookbookResponse {
  hardware: HardwareInfo;
  context_tokens: number;
  recommendation: string;
  models: CookbookModel[];
}

// GET /v1/hf/models — same fit scoring, catalog sourced from the Hugging Face Hub.
export interface HfModelEntry
  extends Omit<CookbookModel, "params_b" | "quant" | "need_gb" | "source"> {
  source: "hf";
  params_b: number | null;
  quant: string | null;
  need_gb: number | null;
  downloads: number | null;
  likes: number | null;
  lastModified: string | null;
  pipeline_tag: string | null;
  library_name: string | null;
}

export interface HfCookbookResponse {
  hardware: HardwareInfo;
  context_tokens: number;
  search: string;
  models: HfModelEntry[];
  count: number;
}

// ───────────── HF model browser (two-pane detail) ─────────────
// Per-quant GGUF fit verdicts from GET /v1/hf/models/{repo_id}.
export type HfFitVerdict =
  | "fits_fully"
  | "fits_cpu_offload"
  | "likely_too_large"
  | "cpu_only"
  | "unknown";

export interface HfQuantOption {
  quant: string;
  size_bytes: number;
  filenames: string[];
  is_sharded: boolean;
  fit: { verdict: HfFitVerdict; score: number; need_gb: number | null; rationale: string };
}

// GET /v1/hf/models/{repo_id} — full metadata + per-quant fits for one repo.
export interface HfModelDetail {
  repo_id: string;
  downloads: number;
  likes: number;
  last_modified: string | null;
  description: string | null;
  params_b: number | null;
  arch: string | null;
  pipeline_tag: string | null;
  library_name: string | null;
  capabilities: string[];
  formats: string[];
  quants: HfQuantOption[];
  has_gguf: boolean;
  context_tokens: number;
  hardware: HardwareInfo;
}

// Left-pane list rows — the subset of HfModelEntry the browser list renders.
// NOTE: the list endpoint does NOT return `capabilities`, so list rows show
// name + stats + relative date only; capability pills appear in the detail pane.
export interface HfModelSummary {
  id: string;
  params_b: number | null;
  downloads: number | null;
  likes: number | null;
  lastModified: string | null;
  pipeline_tag: string | null;
}
