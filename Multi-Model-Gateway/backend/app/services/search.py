"""Web search + page fetching, shared by the web_search/fetch_page tools
and the deep-research orchestrator.

Search uses a self hosted SearXNG instance when SEARXNG_URL is set,
otherwise DuckDuckGo's HTML endpoint cus no API key needed :).
"""
import html as html_lib
import json
import logging
import re
from urllib.parse import parse_qs, urlsplit

from app.core.config import settings
from app.core.metrics import search_degraded_total
from app.services.egress import Policy, fetch, fetch_text

logger = logging.getLogger(__name__)

# the SSRF guard lives in app.services.egress now (F5/F6): `fetch_page` pins the
# resolved IP and streams a capped body; the search backends go through the same
# seam so a user-supplied query cannot bypass the guard. Removing the local
# resolve/validate here means a future caller cannot skip the guard (it can only
# go through egress).

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style|noscript|svg|head)\b.*?</\1>", re.S | re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")
_DDG_TITLE_RE = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIPPET_RE = re.compile(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', re.S)

_USER_AGENT = "Mozilla/5.0 (compatible; llm-gateway/1.0)"


def _clean(fragment: str) -> str:
    return html_lib.unescape(_TAG_RE.sub("", fragment)).strip()


def _resolve_ddg_url(href: str) -> str:
    # result links are redirects of the form //duckduckgo.com/l/?uddg=<urlencoded target>
    if "uddg=" in href:
        target = parse_qs(urlsplit(href).query).get("uddg", [""])[0]
        if target:
            return target
    return href


async def _searxng(query: str, limit: int) -> list[dict]:
    # SEARXNG_URL is a configured host (may be internal) — policy INTERNAL.
    raw = await fetch(
        f"{settings.SEARXNG_URL.rstrip('/')}/search",
        policy=Policy.INTERNAL,
        params={"q": query, "format": "json"},
    )
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        data = {}
    results = data.get("results", []) if isinstance(data, dict) else []
    if not results:
        # a 200 with zero hits is a degraded search, not a success (R6)
        logger.warning("SearXNG returned zero results for query '%s'", query)
        search_degraded_total.labels(source="searxng").inc()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in results[:limit]
    ]


async def _duckduckgo(query: str, limit: int) -> list[dict]:
    # html.duckduckgo.com is a fixed public host — policy INTERNET.
    page = await fetch_text(
        "https://html.duckduckgo.com/html/",
        policy=Policy.INTERNET,
        method="POST",
        form={"q": query},
    )
    titles = _DDG_TITLE_RE.findall(page)
    if not titles:
        # 200 with no result links: DDG quietly degraded (rate-limited page,
        # bot wall, or genuinely nothing) — surface it instead of returning []
        logger.warning("DuckDuckGo returned zero results for query '%s'", query)
        search_degraded_total.labels(source="duckduckgo").inc()
    snippets = [_clean(s) for s in _DDG_SNIPPET_RE.findall(page)]
    return [
        {
            "title": _clean(title),
            "url": _resolve_ddg_url(href),
            "snippet": snippets[i] if i < len(snippets) else "",
        }
        for i, (href, title) in enumerate(titles[:limit])
    ]


async def search(query: str, limit: int | None = None) -> list[dict]:
    """Top web results as [{title, url, snippet}]."""
    limit = limit or settings.WEB_SEARCH_MAX_RESULTS
    if settings.SEARXNG_URL:
        return await _searxng(query, limit)
    return await _duckduckgo(query, limit)


async def fetch_page(url: str, max_chars: int | None = None) -> str:
    """Fetch a URL and return its visible text (tags stripped, whitespace
    collapsed), truncated to max_chars.

    Delegates to services.egress (policy=internet) so the SSRF guard + pinned-IP
    connect (F5) + byte-capped body (F6) cannot be skipped. EgressError is mapped
    to a generic string; callers (research) catch Exception and fall back to a
    snippet, so a refusal simply means "no content".
    """
    from app.services.egress import EgressError

    max_chars = max_chars or settings.RESEARCH_PAGE_MAX_CHARS
    try:
        raw = await fetch_text(url, policy=Policy.INTERNET)
    except EgressError as exc:
        logger.warning("fetch_page refused for %s: %s", url, exc)
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_page failed for %s: %r", url, exc)
        return ""

    # Strip tags/scripts, collapse whitespace, truncate.
    text = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _NL_RE.sub("\n\n", "\n".join(line.strip() for line in text.splitlines())).strip()

    if len(text) > max_chars:
        text = text[:max_chars] + "\n[truncated]"
    return text
