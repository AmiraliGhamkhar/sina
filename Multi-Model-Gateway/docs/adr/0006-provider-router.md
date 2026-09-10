# 0006 — ProviderRouter: one decision for provider resolution

`ProviderRouter.resolve(request, user_id, db) → Resolved` is the single place that
decides which provider + model serves a request. `Resolved` is a frozen dataclass:
`(provider: LLMProvider, model: str, role: "local" | "cloud")`. `chat.py`,
`agent.py`, `research.py`, and Smart Suggest all call this one method instead of
re-implementing the fallback chain.

`resolve_role(text, provider_choice, is_private, message_count)` is a **pure**
function (no DB, no IO): a `match/case` mirror of the original heuristic —
`private` → local; an explicit local/cloud choice wins; a coding keyword in the
last message → cloud; `/` in the model name → cloud; >80 messages → cloud;
default → local. Because it's pure it's unit-testable without a DB or a live
provider.

## The hidden fallback chain

`resolve()` is a three-tier chain, each tier a private function:

1. `_resolve_pinned` — an explicit `provider_id` row (openai_compat/openai/
   anthropic/google/openrouter), ownership-checked, with the row's `default_model`
   used when the request says `model="auto"`.
2. `_resolve_default_for_role` — the user's per-role default provider row.
3. `_fallback(role, request)` — env-config local (`OpenAICompatProvider` on
   `settings.LM_URL`) or cloud (`OpenRouterProvider` on `get_openrouter_api_key()`).

A missing `default_model` on a pinned/default row with `model="auto"` raises
`ProviderConfigError` rather than silently picking something; a missing OpenRouter
key raises `RuntimeError` (the provider path is optional, but once you ask for
cloud you must configure it).

## Why a dedicated router (vs. `get_provider` scattering)

`services/router.py::get_provider` used to own this plus the local-vs-cloud
normalization and the `resolve_role` keyword list. The decision is a real seam
with its own error contract and its own test surface; keeping it in one module
means the pinned/default/fallback ordering and the keyword set change in exactly
one place. The deletion test is the tell: deleting this scatters the chain and
heuristic keywords back into `chat.py`, `agent.py`, `research.py`, and suggest.

Two depths: the router hides the fallback chain; the `LLMProvider` adapters
(`openai_compat`, `openrouter`, `openai`, `anthropic`, `google`) hide wire
differences. Two adapters justify each seam.

`routers/chat.py` resolves directly via `ProviderRouter().resolve()`; `get_provider`
in `services/router.py` is now a thin shim over it (kept for backward compat).

See `services/provider_router.py` and `tests/test_routing.py` (`ResolveRoleTests`,
`GetProviderTests`).
