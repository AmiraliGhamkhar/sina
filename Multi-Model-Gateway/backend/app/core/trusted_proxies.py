"""Resolve the real client IP behind trusted reverse proxies. Stdlib only."""

import ipaddress


def resolve_client_ip(peer, forwarded, trusted_networks) -> str:
    """Return the client IP to rate-limit / log against.

    - peer is falsy or not parseable as an IP address -> "unknown"
    - peer falls inside one of trusted_networks AND forwarded
      (X-Forwarded-For) is present -> the first hop of forwarded,
      i.e. the original client the trusted proxy saw
    - otherwise -> peer as-is (direct connection or untrusted proxy)
    """
    if not peer:
        return "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return "unknown"
    if forwarded and any(peer_ip in net for net in trusted_networks):
        return forwarded.split(",")[0].strip()
    return peer
