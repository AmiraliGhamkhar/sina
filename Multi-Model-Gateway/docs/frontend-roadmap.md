# Frontend Roadmap — Web SPA first (polish), Mobile deferred

## Current focus (2026-09): polish the existing web SPA

The React/Vite SPA (`frontend/`, ~11.7k lines: React 19 + Tailwind 4 + zustand + vitest) is
the **primary client** for the JPMC 2027 Data & AI application window — it is the demoable
artifact. Order of work:

1. **Phase P0 — polish pass (now).** Fix what the 2026-09-06 web-interface-guidelines review
   found BEFORE any new features (batches P0.1–P0.6 below).
2. **Phase F1 — JD-driven feature additions (after P0).** Usage/governance dashboard, eval
   scorecards, governance views. Each consumes backend Phase 6 endpoints
   (`docs/backend-roadmap.md`). Contract first: define the read-API shape in the "Backend
   contract additions" section of this file, build pages against seeded dev data, then land
   the backend pipeline that feeds them.

## Phase P0 — Web SPA polish (from the 2026-09-06 guidelines review)

Systemic findings: **no** `aria-live`/`role="status"`/`role="alert"` anywhere in the SPA; no
`aria-expanded` on any disclosure/dropdown; hover-only reveals without focus equivalents; no
`prefers-reduced-motion` support; Modal has no focus management; no error boundary; several
pages lack an `<h1>`.

### P0.1 — Foundations (global)
- `prefers-reduced-motion` media query gating all keyframes in `src/index.css`
  (fade-in / slide-up / scale-in / pulse-dot / shimmer / branch-flash).
- Error boundary around routes (`src/main.tsx`) with a styled fallback + retry.
- Skip-to-content link; label the `<main>` landmark (`src/components/layout/AppShell.tsx:43`).
- `:focus` → `:focus-visible` consistency (`src/components/ui/Input.tsx:17` rings on mouse focus).
- `tabular-nums` on every token/number chip; `text-wrap: balance` on headings (`index.css`).
- Self-host the three Google fonts (render-blocking stylesheet today) or preload them.
- `body { background-attachment: fixed }` (`index.css:89`) → fixed pseudo-element (mobile
  scroll repaints).

### P0.2 — Design-system a11y (`src/components/ui/`)
- **Modal.tsx**: initial focus + focus trap + focus restore; `aria-labelledby` to the title;
  `overscroll-behavior: contain` on scrollable content.
- **Dropdown.tsx:64-136**: full listbox semantics — `aria-expanded`/`aria-haspopup`,
  `role="listbox"`/`option`/`aria-selected`, arrow-key navigation + typeahead, focus return
  to trigger on Esc.
- **Toggle.tsx:28-40**: the switch has no accessible name (the wrapping `<label>` does not
  name a button) — wire `aria-labelledby` to the label text.
- **Input.tsx**: `aria-invalid` + `aria-describedby` linking error/hint to the input;
  **PasswordInput** visibility toggle `tabIndex={-1}` (`Input.tsx:79`) is keyboard-unreachable.
- **Toaster.tsx:24** + `stores/ui-store.ts:25`: `aria-live` container (assertive for errors);
  error toasts must outlive the fixed 4200 ms auto-dismiss; hover-pause.

### P0.3 — Composed-surface a11y
- **[P0] `src/components/chat/ModelSelector.tsx:61`** — the model flyout opens on hover ONLY
  (`invisible … group-hover/row:visible`): keyboard users cannot pick a specific model. Make
  it click/keyboard-toggled with `aria-expanded`.
- Hover-reveal bars need focus-reveal (`focus-visible:opacity-100`): `MessageBubble.tsx:38`,
  `ConversationList.tsx:141-154`, `pages/images.tsx:52-88` (Download link, Fullscreen, Copy
  also need `aria-label`s — they carry `title` only).
- Icon-only controls missing names: private toggle `ChatInput.tsx:81`; seed number input
  `images.tsx:290` (placeholder-only, no label); panel/page buttons at
  `settings/PresetPanel.tsx:39,100`, `TemplatePanel.tsx:26,78`, `WorkflowPanel.tsx:27,77,99`,
  `ProviderPanel.tsx:39,84`, `models/LocalModels.tsx:72,103`, `ModelList.tsx:90,111`,
  `agent/ToolStepCard.tsx:28`, `agent/WorkspacePanel.tsx:76`, `pages/agents.tsx:55`,
  `pages/models.tsx:264`, `pages/presets.tsx:64`, `pages/templates.tsx:63`,
  `pages/workflows.tsx:56`, `pages/research.tsx:195`, `pages/settings.tsx:269`.
- `aria-expanded` + `aria-controls` on the "Advanced Options" disclosure (`images.tsx:171`).
- `role="alert"` on error banners: `MessageList.tsx:122`, `login.tsx:76`, `register.tsx:105`;
  `role="status"` on the "Generating…" indicators (`ChatInput.tsx:138`).
- RightSidebar tab groups → `role="tablist"`/`tab`/`aria-selected` (`RightSidebar.tsx:156-190`);
  collapsed-rail icon NavLinks in `Sidebar.tsx:143-201` have `title` but need `aria-label`.
- Clear image history (`images.tsx:367`) is destructive without a ConfirmDialog.
- Auth submits disabled until fields are filled (`login.tsx:82`, `register.tsx:116`) — prefer
  enabled submit + inline validation per guidelines.
- Rename inputs lack `aria-label`s (`ConversationList.tsx:110`, `ChatHeader.tsx:65`).

### P0.4 — Headings & copy
- Pages missing `<h1>`: chat (no heading at all), agent, agents, research, presets,
  templates, workflows (images/marketplace/models/not-found/providers/settings have one).
- Title-case audit per Chicago; `…` not `...`; loading strings end with `…`; specific CTA
  labels ("Delete conversation", not "Confirm").
- Adopt the `EmptyState` component everywhere (conversation list, image history, tables).

### P0.5 — Resilience & scale
- truncate / line-clamp audit on long titles, prompts, and JSON views; `min-w-0` on flex
  children carrying long content.
- Conversations list: wire the backend D4 `limit`/`offset` pagination (currently unfetched
  pages); MessageList windowing/virtualization for long threads (Markdown + highlight.js
  per message is heavy).
- Unsaved-changes guards on presets/templates/workflows/providers forms.
- Replace the magic hex `bg-[#3A2D5E]` user bubble (`MessageBubble.tsx:120`) with a token;
  single source for provider dot colors (`lib/utils.ts` has two: `getProviderInfo` +
  `PROVIDER_DOT`); distinct icons for Agent vs Agents and Marketplace vs Models
  (`Sidebar.tsx:34-42` reuses Bot and Library).

### P0.6 — Verification
- Keyboard-only pass over every page; axe/Lighthouse a11y score ≥ 95; visual check with
  `prefers-reduced-motion: reduce`; responsive pass at 480/768/1280; vitest coverage for the
  touched primitives.

## Phase F1 — JD-driven additions (after P0; consumes backend Phase 6)

- **Usage & governance dashboard** (new route): spend/cost per conversation and model,
  local-vs-cloud model mix, latency percentiles, routing decisions, provenance drill-down
  (consumes backend P6.1). This is the interview demo centerpiece.
- **Eval scorecards UI**: per-preset scores, A/B results with uplift + significance
  (consumes backend P6.2).
- **Governance views**: memory browser with redaction status; audit log of tool-permission
  grants (consumes backend P6.4).
- Define each read-API contract in "Backend contract additions" below before building UI.

## Deferred: native mobile app (Expo / React Native)

The plan below is **deferred, not cancelled** — revisit after the application window. It
assumed the mobile app would replace the web frontend; that framing is now inverted (the
web SPA is primary). Everything below is retained as written for the eventual port.

The backend is a self-hosted inference gateway (LM Studio chat, ComfyUI images, OpenRouter,
agent + deep research) reached from a phone over Tailscale. The existing React/Vite web frontend
(`frontend/`) works but isn't the right form factor for phone use — the eventual goal is a
**native mobile app**. This part of the roadmap builds a new Expo/React Native app in a
`mobile/` directory. The web frontend remains the primary client meanwhile.

**Decisions settled (unchanged from when the mobile plan was written):**
- **Stack: Expo + React Native + TypeScript.** Reuses existing TS knowledge and ports the API
  contract layer (types, SSE parsing, Zustand stores) from the web app.
- **Platform: Android-first.** iOS can follow from the same codebase.
- **Scope: full feature parity.** Chat + agent + research (as in-chat modes) + image generation
  + presets/templates/workflows + models + settings + auth.
- **IA:** Chat ↔ Image are the two primary surfaces (bottom tabs). Agent + Deep Research are
  **modes of chat** (a toggle in the composer). Presets/templates/workflows are config screens.
  Model fit-checking is a utility under settings (the web app consolidated
  it into the Models window — Local | Cloud tabs + hardware chip in the tab bar).
- **Self-hosting/onboarding is not a concern.** The app talks to one known gateway URL entered
  once on first launch.
- **Polish level: simple, clean, native.** Favor Expo built-in components, minimal custom
  animation, one dark theme.

## Backend contract additions (from backend Phase 1 + 1b)

- **Agent SSE `done.conversation_id` is now `string | null`** — the backend emits
  `{"type":"done","conversation_id":null}` when the agent 404s/403s before the runtime starts
  (e.g. a non-existent or foreign agent). The typed union in `lib/agent-events.ts` (ADR-0007)
  widens it to `string | null`; the consumer in `use-agent.ts` already null-coalesces
  (`?? convoIdRef.current`), so no UI change is required — but any new parser must accept a null
  id on the terminal `done` event rather than dropping the frame.

- **Mutating `memory_*` tools are now deny-by-default (F7).** `GET /v1/agent/tools` reports
  `first_party: false` for `memory_write`, `memory_str_replace`, `memory_append`, and
  `memory_delete`, so the agent is no longer *offered* them unless the user grants them via
  `PUT /v1/agent/tools/{name}/permission` (the tools panel already exposes this grant). A client
  showing "memory tools" as always-on should now surface them as grant-required. `memory_read`
  is unchanged (still allowed by default).

The backend now supports **bring-your-own-key providers** — user-configured provider rows with
encrypted keys, routed through the same chat/agent/research paths with a legacy env-var
fallback. The contract additions the mobile app must handle:

- **Convo ownership errors normalized (D1):** `GET/PATCH/DELETE /v1/convo/{id}` now all go
  through the same ownership helper — 404 when the conversation doesn't exist and 403 when it
  belongs to another user. Status codes are unchanged; the 404 detail string for
  `GET /v1/convo/{id}` changed from `"conversation not found"` to `"convo not found"` — treat
  both statuses generically and don't match on detail strings.

- **Optional pagination on list endpoints (D4):** `GET /v1/convo`, `GET /v1/presets`, and
  `GET /v1/templates` now accept optional `limit` (1–200) and `offset` (≥0) query params.
  Response shapes are unchanged (`{"data": [...]}` for presets/templates, bare array for
  convo); omitting `limit` keeps the previous no-limit behavior. Clients may pass
  `?limit=N&offset=M` to page through long lists.

- **New endpoints (plain JSON CRUD, no SSE):**
  - `GET /v1/providers` — list this user's providers (seeds the default env-based rows on
    first visit if none exist).
  - `POST /v1/providers` — create (name, type, role, base_url, api_key, default_model,
    is_default, enabled).
  - `PATCH /v1/providers/{id}` — update (api_key `null`/omitted = leave unchanged).
  - `DELETE /v1/providers/{id}`.
  - `POST /v1/providers/{id}/test` — one-token round-trip; returns `{ok, model}` or
    `{ok: false, error}`.
- **`ProviderOut` shape:** `id`, `name`, `type` (`openai_compatible|openai|anthropic|google|openrouter`),
  `role` (`local|cloud`), `base_url`, `default_model`, `is_default`, `enabled`, `created_at`,
  `api_key_masked`. Keys are **write-only** — responses never contain plaintext, only a masked
  suffix.
- **`ChatRequest`** (shared by `/v1/chat/completions` AND `/v1/agent/chat`) now accepts an
  optional **`provider_id`** to pin a specific configured provider row (overrides all routing
  heuristics).
- **Routing** now prefers configured provider rows (per-role defaults), falling back to the
  legacy env-var clients when no rows exist. Seeding is **idempotent**: missing rows are
  created on registration and again on the first providers page visit (existing rows are never
  touched). A "Local (LM Studio)" row is seeded when `LM_URL` is configured; an "OpenRouter"
  row is seeded **only** when an OpenRouter API key is configured (no key => no OpenRouter row).
- **OpenAI-compatible `base_url` normalization:** the adapter automatically appends `/v1` when
  the URL doesn't already contain it (e.g. `http://host:1234` becomes `http://host:1234/v1`),
  so user-entered and seeded URLs work without a manual `/v1` suffix.
- **Anthropic + Google adapters** exist for plain chat but do **not** support tool calling yet
  (agent mode is limited to OpenAI-wire providers: openai_compatible / openai / openrouter).
- **Mobile/web implications:**
  - Add a **"Providers" settings screen** (list/add/edit/delete + test button; fields above;
    masked key display; key input write-only).
    - **Web SPA status: DONE.** `frontend/src/pages/providers.tsx` + `components/settings/ProviderPanel.tsx`
      + `ProviderForm.tsx` + `stores/provider-store.ts` implement the full screen — list/create/
      edit/delete/test against `/v1/providers`, masked-key display, type + role badges,
      default + enabled toggles, and a live connection test with inline result. The mobile
      port must mirror this handling.
  - Optionally a **provider picker in the chat composer** that sends `provider_id` alongside
    the existing preset/mode controls.
  - Update the ported `types.ts` / `api-endpoints.ts` with the provider endpoints and
    `ChatRequest.provider_id`.

### Auth register behavior (S4 + S7)

`POST /auth/register` changed in the backend security pass:

- **No enumeration (S4):** the response is now identical for existing and new emails —
  `200 {"message":"user created successfully"}` either way. The SPA must **not** use the
  register response (code or body) to tell whether an address is already registered.
- **Registration can be disabled (S7):** when the gateway operator sets
  `REGISTRATION_ENABLED=false`, register returns `403 {"detail":"registration disabled"}`.

- **Mobile/web implications:** treat any non-2xx register response as a **generic error**
  (e.g. "Signup failed — check the address and password" / "Registration is disabled").
  Do not surface backend detail strings as account-existence hints, and do not special-case
  a 400 as "email already taken" (that code is no longer emitted by the backend).
- **Web SPA status: DONE.** `frontend/src/pages/register.tsx` now maps 403 →
  "Registration is disabled.", falls back to a generic "Signup failed — check the
  address and password." for every other non-2xx, and shows a neutral success toast
  ("Please sign in with your credentials.") since a 200 no longer means an account
  was created. The mobile port must mirror this handling.

### Research job provider/model now resolved (R4)

`POST /v1/research` no longer stores `provider: "auto"` / `model: "auto"` on the job: the
backend resolves the concrete role (`local` | `openrouter`) and chat model at submit and
persists them on the job row, returning **503 "no capable chat model configured for
research"** when nothing resolves (nothing is enqueued in that case). The response shape
(`{job_id, status}`) and the research SSE event schema are unchanged — the mobile/web
research UI may simply show the resolved role/model instead of "auto" when displaying job
details.

### Chat/agent messages now carry token provenance (R3)

Chat and agent `messages` rows gain a nullable `token_provenance` field (`exact` |
`chunk_count` | null) describing how `tokens_used` was derived; the local chat path
additionally syncs exact counts off-path after the response. This is stored metadata —
the `/v1/chat/completions` and `/v1/agent/chat` SSE wire formats are unchanged.

### Agent memory files (M1)

The backend gained a per-user, versioned **memory file store** (Claude-style)
read/edited by five new first-party agent tools: `memory_read`,
`memory_write`, `memory_str_replace`, `memory_append`, `memory_delete`. Two
frontend notes:

- **The agent tool list is dynamic** — no SPA change required. The agent
  tool-permissions screen (and the backend `GET /v1/agent/tools` listing) picks
  up the new tools automatically; they are first-party, so they are allowed by
  default.
- **Chat/agent prompts now include a memory index** — no SSE contract change.
  `POST /v1/chat/completions` and `POST /v1/agent/chat` inject a leading
  system message (or merge into the preset system prompt) with the user's
  memory-file index and full Tier-1.5 files; the SSE wire formats are
  unchanged, so existing clients stream them as ordinary system context.

### Agent memory files (M2) — backend-only, no contract change

The backend gained a **background curation pipeline** (M2): after each
chat/agent turn an arq job (`run_memory_curation`) reads the transcript,
asks the batch model for memory-file operations, and applies them through the
same versioned `memory_*` primitives. This is entirely server-side — no new
endpoints, no SSE schema changes, and the SPA/mobile clients see memory files
grow over time without any code change.

### Agent tool: generate_image returns /v1/images/file URLs

The agent now has a first-party **`generate_image`** tool (ComfyUI-backed). Its
`tool_result` content is JSON `{"prompt_id", "images":[{filename, url}]}` where each
`url` is a **same-origin relative path** (`/v1/images/file?...`, the authed gateway
route — never a ComfyUI URL). The SPA agent-tool card renderer should detect this shape
and render the images (e.g. `<img src={url}>` with the existing `apiClient` auth header
handling); plain markdown rendering of the JSON is a degraded fallback. Frontend work is
a follow-up batch — the backend change is additive and does not alter the agent SSE
schema (`tool_result` events already carry arbitrary content strings).

### Auth hardening (S-A)

Small backend auth-pass with two contract notes:

- **`GET /v1/images/aspect-ratios` now requires auth** (a valid access token, like every
  other `/v1` route). Response shape unchanged. **SPA impact: none** — the web client's
  `apiClient.request()` attaches the `Authorization: Bearer` header automatically, and the
  aspect-ratio grid already goes through `imageApi.aspectRatios()`, so no code change was
  needed. The mobile port must keep sending the token on this call (it already does via the
  ported `api-endpoints.ts`).

### Workspace + File edits + Undo (T3 — Agents)

Per-user-per-agent git-backed workspace on the named volume `workspaces:/workspaces`
at `workspaces/{user_id}/{agent_id}` (ADR-0002). File tools (`list_files`, `read_file`,
`write_file`, `edit_patch`, `edit_lines`) + `bash` (ADR-0003) operate only inside the
workspace; out-of-workspace paths and absolute paths are 422. `edit_lines`/`write_file`/`edit_patch`
take per-line sha1 hashes from `read_file` (field `lines:[{n, hash, text}]`); a stale
hash is `409 file changed, re-read` (same contract as `memory_files` `if_version`).
Quotas `SANDBOX_DISK_QUOTA_MB` / `SANDBOX_EXEC_TIMEOUT` enforced in the backend; deny-by-default
for file/bash tools via `tool_permissions` + master `ENABLE_CODE_EXECUTION` switch (ADR-0002 Q8).

New backend contract (all `GET`/`POST …/workspace/*` require `Authorization: Bearer` and
owner-or-installer on the agent; `DELETE …/install` removes the installer's pointer only):

- `GET /v1/agents/{id}/workspace/files?path=.` → `{files: string[]}` via `WorkspaceStore.list_files`.
- `GET /v1/agents/{id}/workspace/file?path=...` → `{content, lines:[{n, hash, text}]}` via `read_file`.
- `GET /v1/agents/{id}/workspace/edits?limit=&offset=` → `{data: FileEdit[]}` where `FileEdit`
  is `{id, user_id, agent_id, store, path, patch, before_hash, after_hash, tool_call_id, created_at}`.
- `POST /v1/agents/{id}/workspace/undo {edit_id}` → `{edit_id: newId, undone: editId, commit_sha: string|null}`; 404 when
  the edit doesn't exist and 403 when `file_edits.user_id != caller` (per-running-user
  isolation — you can't undo another user's workspace). Undo is `git revert` of the commit
  that mentions the original `edit_id` plus a new audit row.
- Tool `tool_result` content for `write_file`/`edit_patch`/`edit_lines` is
  `{"edit_id", "path"}` (JSON, stringified in the SSE `tool_result.content` field). Client
  should `JSON.parse(ev.content)?.edit_id` or fall back to an `"ok <edit_id>"` prefix to
  link a `ToolStepCard` to the history entry; `ToolStepCard` renders `DiffView` for these
  tools when `patch` is present, otherwise the raw JSON. The agent SSE schema is unchanged
  (one JSON object per `data:` line: `tool_call`/`tool_result`/`token`/`error`/`done`).

Frontend notes (web SPA — DONE in this slice; mobile must mirror):

- `frontend/src/lib/api-endpoints.ts:248` `workspaceApi.files/file/edits/undo`.
- `frontend/src/stores/workspace-store.ts:1` `useWorkspaceStore {files, edits, fetchFiles, fetchEdits, fetchAll, undo}`.
- `frontend/src/components/agent/DiffView.tsx:1` green/red unified-diff renderer.
- `frontend/src/components/agent/HistoryTimeline.tsx:1` `file_edits → [Undo]` (calls `workspace-store.undo`).
- `frontend/src/components/agent/WorkspacePanel.tsx:1` file browser + file viewer + history (backed by `workspace-store`).
- `/agent` route: `components/layout/RightSidebar.tsx:39` exposes Tools | Workspace tabs when
  `kind === "agent"`; `hooks/use-agent.ts:8` extends `AgentStep` with `edit_id?: string`
  (extracted from every `tool_result` via `JSON.parse(...).edit_id` or `"ok "` prefix) for the
  card↔history link.

### Auth hardening — claims + policy (audit INFO)

Backend audit-INFO pass with two client-visible notes:

- **Registration password policy changed.** `POST /auth/register` now rejects passwords
  shorter than 8 characters or lacking a letter **or** a digit — 422
  `"password must be at least 8 characters with at least one letter and one number"`.
  The web SPA mirrors the rule in `frontend/src/pages/register.tsx` `validate()` (and the
  PasswordInput hint now reads "At least 8 characters with a letter and a number").
- **Existing sessions are invalidated once.** New JWTs carry `iss`/`aud` claims and every
  decode validates them, so tokens issued before the change are rejected — every client
  re-logs in a single time after the backend deploys (the 401-refresh-retry path in
  `api-client.ts` already handles this: the refresh fails → the user is sent to login).
- **No other SPA contract changes.** The SSE event schemas for chat (`[token]` / `[DONE]` /
  `[ERROR]`), agent (`tool_call`/`tool_result`/`token`/`error`/`done`), and research
  (`progress`/`done`/`error`) are unchanged; the new per-user stream
  cap (429 "too many concurrent streams") only appears when one account holds
  `MAX_CONCURRENT_STREAMS` (default 4) streams at once.
- **Login/refresh accept an optional `device_id`.** `POST /auth/login` and
  `POST /auth/refresh` bodies may include `device_id` (string, max 128 chars) — the backend
  binds refresh tokens to it for replay protection and 401s a bound token used from a
  different/missing device id. **SPA status: DONE.** `frontend/src/lib/device-id.ts`
  exports `getDeviceId()` (persistent per-browser id in its own `llm-gateway-device-id`
  localStorage key, NOT the zustand store), and `api-endpoints.ts` `login()`/`refresh()`
  plus the `api-client.ts` refresh-retry path send it. Legacy clients that omit the field
  keep working (unbound tokens are accepted from any device). No other contract changes.

### HF model endpoint + unified fit scoring

The Models window scores Hugging Face models against the same VRAM
heuristic as the local catalog. New backend contract:

- **`GET /v1/hf/models?search=&limit=&context_tokens=`** (auth required) —
  plain JSON. Query params: `search` (≤200 chars, optional), `limit` (1–50,
  default 10), `context_tokens` (512–262144, optional; defaults to the
  backend's `COOKBOOK_CONTEXT_TOKENS`).
- **Response shape `HfCookbookResponse`:** `{hardware, context_tokens,
  search, models, count}` — same `hardware`/`context_tokens` as
  `/v1/cookbook` but **no `recommendation`** and a `count` instead of it.
- **`HfModelEntry`:** mirrors `CookbookModel` (`id`, `source: "hf"`,
  `verdict`, `score`, `need_gb` (nullable), `rationale`) plus nullable HF
  fields `params_b`, `quant` (always `null`), `downloads`, `likes`,
  `lastModified`, `pipeline_tag`, `library_name`.
- **Web SPA status: DONE — folded into the unified Models window.** The
  former separate page was removed; its functionality lives in the
  **Local** tab of the single Models window (`/models`): a 2048–32768
  context-token selector, the recommendation banner, and the
  installed-model verdict table (Model / Quant / Needs / Verdict / Notes).
     A compact "Detected hardware" chip lives in the top tab bar (fetched via
     `GET /v1/hardware`, persistent across tabs) showing name · total VRAM ·
     total RAM; no free-VRAM metric. The chip sits on the LEFT of the tab bar
     and the Local | Cloud tab buttons are right-aligned (`ml-auto`).
  `HfCookbookResponse`/`HfModelEntry` types and `hfApi.models` were added
  to `frontend/src/lib/types.ts` + `api-endpoints.ts`. The mobile port must
  mirror the tab UI and the nullable `need_gb`/`params_b` handling.

### HF model detail endpoint (per-quant GGUF fit) — backend DONE, SPA DONE

The browser's per-model view has a new backend contract (F1). New endpoint +
response shapes for the model detail screen:

- **`GET /v1/hf/models/{repo_id}`** (auth required, plain JSON) — repo id is
  path-encoded (`org/model`). Query param: `context_tokens` (512–262144,
  optional; defaults to `COOKBOOK_CONTEXT_TOKENS`).
- **Response shape `HfModelDetailResponse`:** `{repo_id, downloads, likes,
  last_modified, description, params_b, arch, pipeline_tag, library_name,
  capabilities: string[], formats: string[] ("GGUF"/"MLX"), quants:
  QuantFit[], has_gguf: bool, context_tokens, hardware}` — `hardware` is the
  same shape as `/v1/cookbook`.
- **`QuantFit`:** `{quant, size_bytes, filenames: string[], is_sharded: bool,
  fit: {verdict, score, need_gb, rationale}}`. Verdicts to map to badges:
  `fits_fully` (green), `fits_cpu_offload` (yellow, "partial GPU offload —
  expect reduced speed"), `likely_too_large` (red), `cpu_only` (gray), and
  `unknown` (gray, "couldn't read model metadata").
- **Capabilities** are a best-effort tag scrape (Vision/Tool Use/Reasoning)
  and are often empty — render the list only when non-empty.
- **SPA status: DONE — new `/models` two-pane model browser.**
  `frontend/src/pages/models.tsx` renders a two-pane layout (list left,
  detail right) driven by `GET /v1/hf/models` + `GET /v1/hf/models/{repo_id}`:
  - **Left pane** (`components/models/ModelList.tsx`): search box + 10/25/50
    results dropdown + Search button; rows show repo id + one-line sub
    (params/downloads/likes via `formatCompact`, relative "N days ago"
    timestamp). Clicking a row sets `?repo=<id>` in the URL and opens the
    detail pane. Loading skeletons / error-retry / empty states included.
    The list endpoint does NOT return `capabilities`, so list rows omit
    capability icons by design — pills appear only in the detail pane.
  - **Right pane** (`components/models/ModelDetail.tsx`): header with repo id
    + clipboard copy (toast), download/like stats, description, metadata
    strip (PARAMS / ARCH / DOMAIN / FORMAT pills), capability pills
    (`components/models/CapabilityBadge.tsx` — Vision=amber eye,
    Tool Use=blue wrench, Reasoning=green brain, unknown=plain badge), and
    Download Options: a 2048–32768 context-token selector that refetches the
    detail, then one row per GGUF quant (`components/models/QuantRow.tsx`) —
    GGUF pill, quant chip, size GB (2 decimals), fit verdict badge
    (fits_fully=green / fits_cpu_offload=amber / likely_too_large=red × /
    cpu_only + unknown=muted) + rationale. The **Download button is rendered
    disabled with a "coming soon" hint — the install/download pipeline is
    deferred** (no backend endpoint exists yet).
  - **Models window wiring:** the former separate page is gone — the two-pane browser
    is the Models window's **Cloud** tab and the **Local** tab carries over
    the installed-model fit list. Row selection stays URL-driven
    (`?repo=<id>`).
  - New types (`HfFitVerdict`, `HfQuantOption`, `HfModelDetail`,
    `HfModelSummary`) in `frontend/src/lib/types.ts`; `hfApi.detail(repo_id,
    context_tokens)` in `api-endpoints.ts`; route + sidebar "Models" nav item
    added. The mobile port must mirror the two-pane browser and the deferred
    download affordance.

## Phases

### Phase 1 — Scaffold + contract layer + auth

1. **Create the Expo project:** `npx create-expo-app@latest mobile --template tabs` (Expo SDK
   52+, Expo Router, TypeScript).
2. **Dependencies:** `zustand`, `react-native-sse` (SSE client for RN — replaces the web app's
   `fetch`+`ReadableStream`), `expo-secure-store` (JWT storage), `@react-native-async-storage/async-storage`
   (gateway URL + image history), `react-native-markdown-display`, `lucide-react-native`,
   `expo-haptics`, `expo-web-browser`, `expo-media-library`.
3. **Port types verbatim:** copy `frontend/src/lib/types.ts` (308 lines, pure TypeScript
   interfaces mirroring the backend) to `mobile/src/lib/types.ts`.
4. **Port + adapt the API client:** `mobile/src/lib/api-client.ts` from
   `frontend/src/lib/api-client.ts` (333 lines):
   - Config from AsyncStorage instead of `import.meta.env.VITE_*`; `API_PREFIX` always `/api`.
   - Non-streaming `fetch` ports directly (401-refresh-retry + `ApiError` + refresh coalescing
     carry over); swap `localStorage` → `expo-secure-store`.
   - **SSE:** replace `streamChat()` + `streamEvents()` with `react-native-sse`'s `EventSource`.
     The parsing logic (split on `\n\n`, `data:` prefix, `[DONE]`, `[ERROR]`) ports as-is;
     `EventSource` delivers `e.data` as the payload string. Signatures stay:
     `streamChat(body, onToken, onDone, onError, signal?)` and
     `streamEvents<T>(method, path, body?, signal?): AsyncGenerator<T>`.
   - Pre-flight token check before opening SSE (RN's `EventSource` doesn't expose HTTP status,
     so no mid-stream 401-retry).
   - Port `aspectRatioShort()`, `formatRelativeTime()`, `isTokenExpired()`,
     `getProviderInfo()` from `frontend/src/lib/utils.ts`. **Do not port
     `getImageDisplayUrl()`** — it was deleted from the web app (S3: images are
     fetched through the authed `/v1/images/file` route, see Phase 3 item 16).
5. **Port endpoint definitions:** copy `frontend/src/lib/api-endpoints.ts` verbatim (thin
   wrappers around `apiClient.request()`).
6. **Port Zustand stores:** `auth-store` (SecureStore instead of localStorage), `chat-store`,
   `preset/template/workflow/research/model/agent/ui-stores` (verbatim), `image-store`
   (AsyncStorage instead of localStorage). Drop `layout-store` (no resizable panels on mobile).
7. **Gateway URL config:** `mobile/src/lib/gateway-config.ts` — `getGatewayUrl()` /
   `setGatewayUrl()` in AsyncStorage. First launch shows an onboarding screen with a single
   text input; "Connect" pings `GET /health`.
8. **Auth screens:** `mobile/app/(auth)/login.tsx` + `register.tsx`, adapting the web pages.
   `initializeAuth()` silently refreshes on app open.
9. **Navigation shell** (Expo Router, file-based in `mobile/app/`):
   ```
   _layout.tsx          → root Stack [onboarding → (auth) → (tabs)]
   onboarding.tsx
   (auth)/_layout.tsx, login.tsx, register.tsx
   (tabs)/_layout.tsx   → bottom tabs: Chat, Images, Settings
     (chat)/index.tsx       → conversation list
     (chat)/[id].tsx        → chat thread
     (images)/index.tsx     → image generation + history
     (settings)/index.tsx   → settings menu
      (settings)/presets.tsx, templates.tsx, workflows.tsx, models.tsx, agent-tools.tsx, gateway.tsx
   ```

### Phase 2 — Chat (with agent + research as modes)

10. **Conversation list:** `FlatList` of conversations, tap to open, "New Chat" header button,
    pull-to-refresh, swipe-to-delete.
11. **Chat thread:** inverted `FlatList` of `MessageBubble`s (markdown via
    `react-native-markdown-display`), streaming content appends to the last assistant bubble.
    Composer: auto-growing `TextInput` + send/stop button + **mode segmented control**
    (Chat / Agent / Research) + preset dropdown + private toggle.
12. **Mode field in chat store:** `mode: "chat" | "agent" | "research"` (default `"chat"`) +
    `setMode`.
13. **Unified send path:** one `use-chat.ts` hook merging the web app's `use-chat.ts` +
    `use-agent.ts` + `use-research-job.ts`. `send(content)` branches on `store.mode`:
    - `chat` → `streamChat` (plain-text SSE).
    - `agent` → `streamEvents("POST","/v1/agent/chat",...)` parsing `AgentEvent`s
      (`tool_call`/`tool_result`/`token`/`done`).
    - `research` → `researchApi.create()` then `streamEvents("GET","/v1/research/{id}/stream")`
      parsing `ResearchEvent`s (`progress`/`done`/`error`).
    All three use an `AbortController`; `isStreaming`/`streamError`/`pendingUserContent` shared.
14. **Inline rendering:** agent tool-step cards (adapt `ToolStepCard.tsx`) + research progress
    card (stage + progress bar + sources) render as ephemeral bubbles above the answer. Both
    are transient (not persisted to the conversation — the backend's agent/research endpoints
    don't write to `messages` today).

### Phase 3 — Image generation

15. **Image generation screen:** `mobile/app/(tabs)/(images)/index.tsx`, adapting
    `frontend/src/pages/images.tsx` (19.3KB):
    - Prompt + collapsible negative-prompt `TextInput`.
    - Aspect-ratio button grid fetched from `imageApi.aspectRatios()` (backend owns the list).
    - Collapsible controls: steps slider, cfg slider, seed input, batch stepper, rewrite
      `Switch`, template + workflow dropdowns.
    - Generate → `useImageGeneration` hook (port from web: polls `imageApi.status(promptId)`
      every 2s up to 90 times).
    - Results: image grid, tap to fullscreen `Modal`, long-press to save via `expo-media-library`.
    - History: `FlatList` from `image-store` (AsyncStorage), tap to re-open.
16. **Image URL fetching (S3 shipped):** image status URLs are now **relative**
    `/v1/images/file?...` and require the auth token. The SPA should use the returned
    `url` directly (same gateway origin, so the normal Authorization header applies) and
    remove any `/view` URL rewriting — the open Caddy `/view*` proxy is gone, so
    `getImageDisplayUrl()` must not rewrite ComfyUI hosts anymore.
    - **Web SPA status: DONE.** `frontend/src/components/images/AuthedImage.tsx` fetches
      bytes through the authed API (with 401-refresh-retry) and renders blob URLs;
      `lib/authed-image.ts` provides the shared cache + `useResolvedImageUrl` hook, and
      `getImageDisplayUrl()` has been deleted from `lib/utils.ts`. The mobile port should
      use the same pattern (RN `Image` supports a `headers` prop, or fetch + blob/base64
      like the web app).

### Phase 4 — Settings + config screens

17. **Settings menu** → Account (email + logout), Gateway (change URL), Presets, Templates,
    Workflows, Models, Agent Tools.
18. **Presets/Templates/Workflows:** CRUD screens adapting the web `PresetForm`/`TemplateForm`/
    `WorkflowForm` to RN inputs. Workflows use a full-screen monospace `TextInput` for the
    ComfyUI JSON graph (no graph editor).
19. **Models (VRAM fit-check):** read-only — `hardwareApi.hardware()` +
    `GET /v1/cookbook` drive the Local fit list, `hfApi` drives the HF
    browser. The web app consolidated both into one Models window (Local |
    Cloud tabs + hardware chip in the tab bar); the mobile port should mirror
    that instead of a separate screen.
20. **Agent tools:** `agentApi.tools()` list + `Switch` per tool bound to `setPermission`.
    First-party allowed by default; MCP deny by default.

### Phase 5 — Polish

21. **Theme:** `mobile/src/theme.ts` with the color tokens from `frontend/src/index.css`
    (deep-purple canvas `#0D0B1E`, coral `#FF8A6A`, gold `#FFC85C`). One dark theme, `StyleSheet`.
22. **Native polish:** `SafeAreaView` (notch/home-indicator), `expo-haptics` (send/image
    complete/error), `KeyboardAvoidingView` (composer above keyboard), `ActivityIndicator`
    loading states, empty states, error banners (reuse `ApiError` classification), simple toast
    system.
23. **Build:** `npx expo run:android` (emulator/USB). Standalone APK via
    `eas build --platform android --profile preview` (sideloaded, no app store).

## Verification

- **Phase 1:** app launches → onboarding → enter gateway URL → `GET /health` → login →
  register → main tabs. Kill + relaunch → silent refresh → main tabs (no login).
- **Phase 2:** send a message → tokens stream in real-time. Toggle Agent → tool cards + answer
  inline. Toggle Research → progress card + synthesized answer + sources. Stop cancels
  mid-stream. Open a conversation → continues the thread.
- **Phase 3:** enter prompt → select ratio → generate → image appears → fullscreen → save to
  gallery. Rewrite toggle on → prompt rewritten. History list populates.
- **Phase 4:** preset/template/workflow CRUD persists. Models shows GPU + fit scores. Tool
  permission toggle respected by the next agent run.
- **Phase 5:** safe areas on all screens. Haptics on send. `eas build` → APK → sideload on a
  phone → enter tailnet gateway URL → full app works over Tailscale.

## Critical files (web app → port from)
- `frontend/src/lib/types.ts` — complete backend contract; ports verbatim.
- `frontend/src/lib/api-client.ts` — SSE: `streamChat` (plain tokens) + `streamEvents`/`_readSseStream`
  (JSON objects) both consume the shared private `_readSseChunks` transport reader; reimplement the
  two interpreters (not the transport) on `react-native-sse`.
- `frontend/src/lib/api-client.ts:102-167` — 401-refresh-retry + coalescing; ports to RN `fetch`.
- `frontend/src/hooks/use-chat.ts` + `use-agent.ts` + `use-research-job.ts` — three streaming
  hooks that merge into one mode-dispatching `use-chat.ts`.
- `frontend/src/lib/authed-image.ts` — `resolveImageUrl()` + `useResolvedImageUrl()`
  (authed blob fetch + cache) and `frontend/src/components/images/AuthedImage.tsx` —
  the pattern the mobile app should follow for S3-compliant image rendering.

## Design-practice cleanup (SSE transport + image composer)
- `lib/api-client.ts` had two SSE transport readers (the `streamChat` inline loop and
  `_readSseStream`). Extracted a shared private `_readSseChunks` generator for the `\n\n`
  boundary split + tail flush; `streamChat` and `_readSseStream` keep only their event
  interpreters. Chat token stream is unchanged.
- Extracted the image-composer state (~13 `useState` + aspect-ratio loading + the "New Image"
  nonce reset + `handleGenerate`/`onComplete`) out of `pages/images.tsx` into
  `hooks/use-image-composer.ts`; the page is now a pure render.

## Assumptions
- **`react-native-sse` is the SSE transport** (supports POST + custom headers). Fallback:
  `@microsoft/fetch-event-source` + ReadableStream polyfill if it breaks on the target RN
  version. Test it first in Phase 1 with a simple chat send.
- **The web frontend is the PRIMARY client until further notice** (JPMC 2027 Data & AI
  application window — see Phases P0/F1 at the top of this file); the mobile app is
  deferred, not cancelled.
- **Provider API is plain JSON CRUD — no SSE.** The provider list/create/update/delete/test
  endpoints use ordinary `apiClient.request()` calls; only chat/agent/research stream.
- **Agent + Research results are ephemeral** in the UI (not persisted to the conversation),
  matching the web app. If persistence is later wanted, that requires a backend change
  (write research answer as a `Message`) — out of scope here.
- **Image files are fetched through the authed `/v1/images/file?...` route (S3).**
  The backend returns relative URLs on job status; the app uses them as-is with the
  normal Authorization header. No `/view` proxying or host rewriting. **The web SPA
  already implements this** — see "Image URL fetching (S3 shipped)" in Phase 3.
- **Gateway URL is a user-entered setting**, not a build-time env var (no rebuild to change
  URL). Fallback: `EXPO_PUBLIC_GATEWAY_URL` EAS env var if a baked-in URL is preferred.
- **Android-first; iOS later** (same codebase; iOS build needs Apple Developer credentials).
- **The web SPA has vitest tests** (`src/lib/agent-events.test.ts`, `api-client.test.ts`,
  `sse-events.test.ts`) — port the interpreter tests to the RN equivalents. RN UI tests:
  React Native Testing Library + Jest, added lazily.
- **Gateway operator onboarding is handled by `setup.ps1`/`setup.sh`** — they generate
  `.env` and start the stack; the mobile app's first-launch onboarding remains a single
  gateway-URL entry.
