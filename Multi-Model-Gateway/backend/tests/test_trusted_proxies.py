"""Unit tests for app.core.trusted_proxies.resolve_client_ip (the S5
X-Forwarded-For trust gate).

Stdlib unittest only — no pytest dependency. The module is stdlib-only
(ipaddress), so these tests import it directly and need no settings or
secret environment.
"""
import ipaddress
import unittest

from app.core.trusted_proxies import resolve_client_ip

# Caddy sits on the Docker bridge (172.16.0.0/12) in the default compose
# layout; that same CIDR is the TRUSTED_PROXIES default in config.py.
DOCKER_NET = [ipaddress.ip_network("172.16.0.0/12")]

# A peer inside the trusted CIDR (as a proxy hop would appear).
TRUSTED_PEER = "172.16.0.5"
# A peer outside every trusted network.
UNTRUSTED_PEER = "198.51.100.7"


class ResolveClientIpTests(unittest.TestCase):
    def test_trusted_peer_uses_first_forwarded_hop(self):
        # proxy chain arrives in right-to-left order: rightmost is the proxy
        # we talked to, leftmost is the original client
        self.assertEqual(
            resolve_client_ip(TRUSTED_PEER, "1.2.3.4, 5.6.7.8", DOCKER_NET),
            "1.2.3.4",
        )

    def test_forwarded_hop_whitespace_stripped(self):
        self.assertEqual(
            resolve_client_ip(TRUSTED_PEER, "  1.2.3.4  , 5.6.7.8", DOCKER_NET),
            "1.2.3.4",
        )

    def test_untrusted_peer_ignores_forwarded(self):
        # a direct client (or a spoofing attacker) supplying X-Forwarded-For
        # must not be able to pick its own rate-limit bucket
        self.assertEqual(
            resolve_client_ip(UNTRUSTED_PEER, "1.2.3.4", DOCKER_NET),
            UNTRUSTED_PEER,
        )

    def test_trusted_peer_without_forwarded_returns_peer(self):
        self.assertEqual(
            resolve_client_ip(TRUSTED_PEER, None, DOCKER_NET),
            TRUSTED_PEER,
        )

    def test_trusted_peer_with_empty_forwarded_returns_peer(self):
        self.assertEqual(
            resolve_client_ip(TRUSTED_PEER, "", DOCKER_NET),
            TRUSTED_PEER,
        )

    def test_none_peer_returns_unknown(self):
        self.assertEqual(resolve_client_ip(None, "1.2.3.4", DOCKER_NET), "unknown")

    def test_empty_peer_returns_unknown(self):
        self.assertEqual(resolve_client_ip("", "1.2.3.4", DOCKER_NET), "unknown")

    def test_malformed_peer_returns_unknown(self):
        self.assertEqual(resolve_client_ip("not-an-ip", "1.2.3.4", DOCKER_NET), "unknown")

    def test_cidr_membership_trusted(self):
        # 172.18.5.5 falls inside 172.16.0.0/12 -> forwarded is honored
        self.assertEqual(
            resolve_client_ip("172.18.5.5", "9.9.9.9", DOCKER_NET),
            "9.9.9.9",
        )

    def test_cidr_non_membership_untrusted(self):
        # 10.0.0.5 is not inside 172.16.0.0/12 -> forwarded is ignored
        self.assertEqual(
            resolve_client_ip("10.0.0.5", "9.9.9.9", DOCKER_NET),
            "10.0.0.5",
        )

    def test_empty_trusted_networks_ignores_forwarded(self):
        # no trusted proxy configured -> never trust X-Forwarded-For, even
        # when the peer would otherwise look like a proxy hop
        self.assertEqual(
            resolve_client_ip(TRUSTED_PEER, "1.2.3.4", []),
            TRUSTED_PEER,
        )
        self.assertEqual(
            resolve_client_ip(UNTRUSTED_PEER, "1.2.3.4", []),
            UNTRUSTED_PEER,
        )


if __name__ == "__main__":
    unittest.main()
