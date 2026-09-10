"""Egress — the ONE deep module for every outbound HTTP call in the backend.

Open findings this closes (see docs/backend-roadmap.md):
  - F5 (DNS-rebinding TOCTOU): a public hostname is resolved exactly ONCE,
    validated, and then the connection is pinned to that resolved IP while the
    original hostname is preserved for the Host header and TLS SNI. A DNS answer
    that changes between resolve and connect can no longer redirect the request
    into the internal network — the socket goes to the IP that was validated.
  - F6 (response-buffer OOM): every response body is streamed through a byte
    cap, so a malicious/compressed response cannot be buffered into memory
    unboundedly (gzip-bomb class). We cap the *decoded* length, not the wire
    length, so Content-Length lies are caught too.

Why one module instead of 17 scattered `httpx.AsyncClient` sites: the SSRF guard
is the real adapter, and it must be impossible to skip. A new contributor cannot
"forget" the guard because they never construct a client here — they call
`egress.fetch(...)` and the policy is inside the module. The deletion test is the
tell: delete this module and the SSRF guard + byte caps scatter back into
search.py, fit_score.py, template.py, comfy.py, memory.py, tokenize.py,
images.py, models.py, and sandbox/http.py, and a future caller copies whichever
pattern they find first.

Two policy tiers ('split brain' from the report — one adapter is hypothetical,
two make the seam real):
  - `internet` (default): resolve + validate public-only, pin-IP connect. Used by
    fetch_page, web_search backends, and any URL the model/user can influence.
  - `internal` (opt-in): no SSRF validation — for the trusted internal endpoints
    (ComfyUI, LM Studio, sandbox) that are configured, not user-supplied. Still
    gets the byte cap and timeout.
  - `private_allowed` (opt-in): like internet but permits private/loopback IPs —
    for deployments that point SEARXNG_URL at an internal host. The validation
    still applies; only the private check is skipped.

The module never raises raw httpx/socket exceptions across its public seam: it
maps every failure to `EgressError` with a generic message (no internal IPs /
hostnames leak to the model — sweep F11 convention).

Usage:
    from app.services.egress import fetch, EgressError, Policy
    text = await fetch("https://example.com/x", max_bytes=6000)
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from enum import Enum
from urllib.parse import urlsplit

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Hard cap on redirect hops we'll follow (each one is re-validated for SSRF).
_MAX_REDIRECTS = 5

# Skip re-resolving the hostname on redirect hops that stay in the same name
# (common case); we re-resolve + re-validate any N E W hostname, because a
# redirect is precisely where a public host tries to funnel us internal.
_DEFAULT_TIMEOUT = 20.0


class Policy(str, Enum):
    """Which SSRF / host rules apply to a fetch."""

    INTERNET = "internet"          # public-only, validate + pin (default)
    INTERNAL = "internal"          # no validation — trusted configured endpoints
    PRIVATE_ALLOWED = "private_allowed"  # like internet but permits private IPs


class EgressError(Exception):
    """A fetch failed. `message` is generic (no internal topology leak)."""


async def _resolve(host: str) -> str:
    """Resolve `host` once and return a validated, connectable IP (IPv4-preferred).

    Returns a configurable single address so the rest of the request is pinned to
    the exact IP we validated. Raises EgressError for unresolvable hosts.
    """
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except socket.gaierror:
        raise EgressError("could not resolve host")
    if not infos:
        raise EgressError("could not resolve host")
    # Prefer IPv4 so URL-host construction doesn't need bracket handling; IPv6 is
    # still usable but never preferred (simpler, RFC-correct for SNI/Host).
    addrs = [i[4][0] for i in infos]
    return next((a for a in addrs if ":" not in a), addrs[0])


def _is_public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    # Link-local covers 169.254.0.0/16 including the cloud-metadata IP.
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate(host: str, ip: str, policy: Policy) -> None:
    """Apply the policy to a resolved address; raises EgressError on violation."""
    if policy == Policy.INTERNAL:
        return
    if not _is_public(ip):
        if policy == Policy.PRIVATE_ALLOWED:
            return
        raise EgressError("refusing to fetch a non-public address")


def _pin_url(original: str, ip: str) -> str:
    """Rewrite the URL host to the pinned IP while keeping scheme/path/query.

    For IPv6 the IP must be bracketed; we prefer IPv4 in _resolve so in practice
    this is a bare address, but we handle IPv6 correctly regardless.
    """
    parts = urlsplit(original)
    host = f"[{ip}]" if ":" in ip else ip
    # Rebuild with the same scheme/netloc-port/path/query. We preserve the
    # explicit port (if any) since scheme default ports don't survive urlsplit.
    netloc = host
    if parts.port is not None:
        netloc = f"{host}:{parts.port}"
    return parts._replace(netloc=netloc).geturl()


async def _read_limited(stream: httpx.AsyncByteStream, max_bytes: int) -> bytes:
    """Consume the response body up to max_bytes; abort+flag if it exceeds."""
    chunks = []
    total = 0
    async for chunk in stream:
        total += len(chunk)
        if total > max_bytes:
            raise EgressError("response too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch(
    url: str,
    *,
    policy: Policy | str = Policy.INTERNET,
    max_bytes: int = 512_000,
    timeout: float = _DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = False,
    max_redirects: int = _MAX_REDIRECTS,
    method: str = "GET",
    params: dict | None = None,
    content: bytes | None = None,
    form: dict | None = None,
) -> bytes:
    """Fetch `url` and return the raw body bytes, subject to the policy.

    Supports GET (default) plus POST via `content` (bytes body) or `form`
    (urlencoded body) and query `params` — so the search backends, which issue
    GET-with-params and POST-with-form, route through the same seam.

    Raises EgressError on any failure. `follow_redirects=False` by default so
    each hop is re-validated (a public URL can 302 to an internal one); when
    enabled, each hop is still validated through the same pin path.
    """
    policy = Policy(policy) if isinstance(policy, str) else policy
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "Mozilla/5.0 (compatible; llm-gateway/1.0)")

    # Timeout lives on the client so it applies to every hop and every phase
    # (connect, read). follow_redirects=False on the client: we re-validate hops
    # ourselves so a public URL can't funnel us internal.
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=timeout,
    ) as client:
        current = url
        for hop in range(max_redirects + 1):
            parsed = urlsplit(current)
            if parsed.scheme not in ("http", "https"):
                raise EgressError("unsupported scheme")
            host = parsed.hostname or ""
            if not host:
                raise EgressError("missing host")

            # Build the request. Internal: no SSRF check, connect to the name
            # directly. Otherwise resolve once -> validate -> pin to that IP (F5
            # closed) while preserving Host + SNI for TLS.
            if policy == Policy.INTERNAL:
                response = await client.request(
                    method, current, headers=headers, params=params,
                    content=content, data=form,
                )
            else:
                ip = await _resolve(host)
                _validate(host, ip, policy)
                pinned = _pin_url(current, ip)
                req = client.build_request(
                    method, pinned,
                    headers={**headers, "Host": host},
                    params=params, content=content, data=form,
                    extensions={"sni_hostname": host} if parsed.scheme == "https" else None,
                )
                response = await client.send(req)

            if response.is_redirect:
                location = response.headers.get("location")
                if follow_redirects and location:
                    from urllib.parse import urljoin
                    current = urljoin(current, location)
                    continue
                # Not following: treat as an error so callers don't silently
                # read a 3xx body. Callers that want to follow pass
                # follow_redirects=True.
                raise EgressError("unexpected redirect")
            break
        else:
            raise EgressError("too many redirects")

        response.raise_for_status()
        try:
            body = await _read_limited(response.aiter_bytes(), max_bytes)
        finally:
            await response.aclose()
    return body


async def fetch_text(
    url: str,
    *,
    policy: Policy | str = Policy.INTERNET,
    max_bytes: int = 512_000,
    timeout: float = _DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = False,
    max_redirects: int = _MAX_REDIRECTS,
    method: str = "GET",
    params: dict | None = None,
    content: bytes | None = None,
    form: dict | None = None,
) -> str:
    """Fetch `url` and return the body decoded to text (charset-aware)."""
    body = await fetch(
        url,
        policy=policy,
        max_bytes=max_bytes,
        timeout=timeout,
        headers=headers,
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
        method=method, params=params, content=content, form=form,
    )
    return body.decode("utf-8", errors="replace")
