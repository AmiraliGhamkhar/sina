"""Shared plumbing for the 9Router adapters (LLM + STT).

9Router (https://github.com/decolua/9router) is a self-hosted proxy that
fronts 40+ upstream AI providers — Claude, GPT, Gemini, Groq, Kilo, Cline,
Copilot and friends — behind one OpenAI-compatible API with automatic
account fallback and rotation.

Two things make it worth a dedicated adapter rather than reusing the generic
OpenAI-compatible configuration:

1. **Route prefix.** 9Router is a Next.js app whose OpenAI-compatible surface
   lives under ``/api/v1/...`` (``POST /api/v1/chat/completions``,
   ``POST /api/v1/audio/transcriptions``, ``GET /api/v1/models``) — *not*
   ``/v1/...``. The shared :func:`normalize_nine_router_base_url` folds every
   plausible operator spelling onto the prefix the transport expects.
2. **``provider/model`` identifiers.** Upstream selection is encoded in the
   model id itself (``claude/claude-sonnet-4``, ``groq/whisper-large-v3-turbo``),
   and the catalog is *dynamic* — it depends on which accounts the operator
   has connected. Model discovery therefore has to be a live call, surfaced to
   the WPF client through ``GET /api/v1/providers/9router/models``.

Trust boundary: 9Router runs inside the deployment (``127.0.0.1:20128`` by
default). It *can* relay to cloud upstreams, so both adapters declare the
privacy class from configuration (``privacy_class``, default ``local``) —
operators who point it at cloud accounts can flip it to ``cloud`` and the
router will keep private encounters away from it.

Auth: 9Router only enforces a key when ``REQUIRE_API_KEY=true``; the key is
accepted as ``Authorization: Bearer …`` or ``x-api-key: …``. We send Bearer.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from ai.base import ProviderError, ProviderUnavailableError
from ai.llm.openai_compat import normalize_base_url

logger = logging.getLogger(__name__)

#: 9Router's own default (``PORT`` env, see its .env.example).
DEFAULT_BASE_URL = "http://127.0.0.1:20128"
#: Next.js route group holding the OpenAI-compatible endpoints.
DEFAULT_API_PREFIX = "/api"
#: 9Router's model kinds; ``llm`` is the default listing, the rest are
#: addressable as ``GET /v1/models/{kind}``.
MODEL_KINDS = ("llm", "stt", "tts", "embedding", "image", "web")


def normalize_nine_router_base_url(
    raw: str, api_prefix: str | None = DEFAULT_API_PREFIX
) -> str:
    """Fold operator input onto ``{origin}{api_prefix}``.

    ``OpenAICompatClient`` appends ``/v1/chat/completions`` to whatever base it
    is given, and 9Router wants ``/api`` in front of that. Accepted spellings
    (all → ``http://h:20128/api``)::

        h:20128            http://h:20128/
        http://h:20128/v1  http://h:20128/api/v1
        http://h:20128/api

    Pass ``api_prefix=""`` for a reverse proxy that already rewrites the path.
    """
    url = normalize_base_url(raw or DEFAULT_BASE_URL)
    prefix = "/" + (api_prefix if api_prefix is not None else DEFAULT_API_PREFIX).strip("/")
    if prefix == "/":
        return url
    if url.endswith(prefix):
        return url
    return url + prefix


def nine_router_headers(api_key: str | None) -> dict[str, str]:
    """Bearer auth when a key is configured; empty dict otherwise.

    9Router accepts requests without a key unless ``REQUIRE_API_KEY=true``, so
    a missing key is a valid configuration, not an error.
    """
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def resolve_model_id(configured: str | None, requested: str | None) -> str:
    """Pick the ``provider/model`` id for a call.

    9Router cannot infer an upstream from a bare model name, so an empty id is
    a configuration error worth reporting clearly rather than a 400 from the
    proxy that says nothing about *our* settings.
    """
    model = str(requested or configured or "").strip()
    if not model:
        raise ProviderError(
            "9router needs a model id of the form 'provider/model' "
            "(set MS_LLM__NINE_ROUTER__MODEL / MS_STT__NINE_ROUTER__MODEL, "
            "or pick one from GET /api/v1/providers/9router/models)",
            provider="9router",
        )
    return model


def parse_models_payload(body: Any, *, kind: str) -> list[dict[str, Any]]:
    """Normalize 9Router's ``{object: "list", data: [...]}`` (or a bare array).

    Tolerates the shapes its upstreams produce: ``data``, ``models``,
    ``results`` or a top-level list; entries as objects with ``id`` or as plain
    strings. Unknown shapes yield an empty list rather than raising — model
    discovery is advisory, never on the transcription path.
    """
    if isinstance(body, Mapping):
        items: Any = body.get("data")
        if items is None:
            items = body.get("models")
        if items is None:
            items = body.get("results")
        if items is None:
            items = []
    elif isinstance(body, list):
        items = body
    else:
        items = []

    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            if item.strip():
                out.append({"id": item.strip(), "kind": kind})
            continue
        if not isinstance(item, Mapping):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        entry: dict[str, Any] = {
            "id": model_id,
            "kind": str(item.get("kind") or kind),
            "owned_by": item.get("owned_by") or _provider_of(model_id),
        }
        for src, dst in (
            ("context_length", "context_length"),
            ("max_completion_tokens", "max_completion_tokens"),
        ):
            value = item.get(src)
            if isinstance(value, (int, float)):
                entry[dst] = int(value)
        caps = item.get("capabilities")
        if isinstance(caps, Mapping):
            entry["capabilities"] = dict(caps)
        out.append(entry)
    return out


def _provider_of(model_id: str) -> str | None:
    """``claude/claude-sonnet-4`` → ``claude`` (9Router's ``owned_by``)."""
    return model_id.split("/", 1)[0] if "/" in model_id else None


async def fetch_models(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    kind: str = "llm",
    headers: Mapping[str, str] | None = None,
    timeout: float = 10.0,
    provider_name: str = "9router",
) -> list[dict[str, Any]]:
    """``GET {base}/v1/models`` (llm) or ``GET {base}/v1/models/{kind}``.

    Error contract mirrors the OpenAI-compatible transport: 401/403 are
    configuration faults (:class:`ProviderError`), 429/5xx are transient
    (:class:`ProviderUnavailableError`).
    """
    suffix = "/v1/models" if kind in ("", "llm") else f"/v1/models/{kind}"
    try:
        resp = await client.get(
            f"{base_url}{suffix}", headers=dict(headers or {}), timeout=timeout
        )
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError(
            f"9router unreachable at {base_url}: {exc}", provider=provider_name
        ) from exc
    if resp.status_code in (401, 403):
        raise ProviderError(
            f"9router rejected the API key while listing {kind} models "
            "(MS_LLM__NINE_ROUTER__API_KEY / MS_STT__NINE_ROUTER__API_KEY)",
            provider=provider_name,
            status_hint=resp.status_code,
        )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise ProviderUnavailableError(
            f"9router model listing failed ({resp.status_code})", provider=provider_name
        )
    if resp.status_code >= 400:
        raise ProviderError(
            f"9router model listing rejected ({resp.status_code}): {resp.text[:200]}",
            provider=provider_name,
            status_hint=resp.status_code,
        )
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 — non-JSON is a config/proxy fault
        raise ProviderError(
            f"9router returned non-JSON from {suffix}", provider=provider_name
        ) from exc
    return parse_models_payload(body, kind=kind)
