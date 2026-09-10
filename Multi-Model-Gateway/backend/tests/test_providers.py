"""Unit tests for app.routers.providers._validate_provider_base_url.

Stdlib unittest only — no pytest dependency. Tests are offline (no DB, no
network): the validator is a pure function over a URL string plus
settings.ALLOW_PRIVATE_PROVIDER_URLS, and every DNS lookup is mocked so no
real resolution ever happens. If the module can't be imported in this
environment (missing settings/secret deps), the whole suite skips cleanly.
"""
import socket
import unittest
from unittest.mock import patch

try:
    from app.routers.providers import _validate_provider_base_url
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    _validate_provider_base_url = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _getaddrinfo_result(*addrs):
    """Build socket.getaddrinfo-style tuples (family, type, proto, canonname,
    sockaddr) for the given IPv4 address strings."""
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0))
        for addr in addrs
    ]


def _public_only():
    """Context manager: the flag the validator reads, set to public-only mode."""
    return patch("app.routers.providers.settings.ALLOW_PRIVATE_PROVIDER_URLS", False)


class ValidateProviderBaseUrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _validate_provider_base_url is None:
            raise unittest.SkipTest(
                f"app.routers.providers import failed in this env: {_IMPORT_ERROR}"
            )

    def test_none_passes(self):
        """No base_url is a valid (absent) configuration — never raises."""
        _validate_provider_base_url(None)

    def test_empty_string_passes(self):
        _validate_provider_base_url("")

    def test_non_http_scheme_rejected(self):
        """ftp:// (and any non-http(s) scheme) is rejected on scheme alone."""
        with self.assertRaises(ValueError):
            _validate_provider_base_url("ftp://example.com")

    def test_missing_hostname_rejected(self):
        """A bare 'http://' has no host to route to — rejected."""
        with self.assertRaises(ValueError):
            _validate_provider_base_url("http://")

    def test_public_https_passes_with_default_flag(self):
        """With ALLOW_PRIVATE_PROVIDER_URLS left True (the default) no DNS
        check runs, so a normal public URL passes without any lookup."""
        with patch(
            "app.routers.providers.settings.ALLOW_PRIVATE_PROVIDER_URLS", True
        ):
            _validate_provider_base_url("https://openrouter.ai/api/v1")

    def test_loopback_rejected_when_private_forbidden(self):
        """Public-only mode: localhost resolving to 127.0.0.1 is loopback."""
        with _public_only(), patch(
            "socket.getaddrinfo",
            return_value=_getaddrinfo_result("127.0.0.1"),
        ):
            with self.assertRaises(ValueError):
                _validate_provider_base_url("http://localhost:1234")

    def test_docker_internal_host_rejected_when_private_forbidden(self):
        """Public-only mode: host.docker.internal resolves to a private LAN
        address (Docker Desktop's 192.168.65.0/24 bridge) — rejected."""
        with _public_only(), patch(
            "socket.getaddrinfo",
            return_value=_getaddrinfo_result("192.168.65.254"),
        ):
            with self.assertRaises(ValueError):
                _validate_provider_base_url("http://host.docker.internal:1234")

    def test_public_host_passes_when_private_forbidden(self):
        """Public-only mode: a genuinely public resolution passes."""
        with _public_only(), patch(
            "socket.getaddrinfo",
            return_value=_getaddrinfo_result("104.18.24.24"),
        ):
            _validate_provider_base_url("https://openrouter.ai")

    def test_dns_failure_passes_when_private_forbidden(self):
        """Documented skip policy: a transient gaierror must not block saving a
        provider — the real connection surfaces an unreachable host instead."""
        with _public_only(), patch(
            "socket.getaddrinfo",
            side_effect=socket.gaierror,
        ):
            _validate_provider_base_url("https://openrouter.ai")

    def test_any_private_address_among_public_rejected(self):
        """Public-only mode: one non-public address in the resolution set is
        enough — the host is rejected even if it also has public addresses."""
        with _public_only(), patch(
            "socket.getaddrinfo",
            return_value=_getaddrinfo_result("104.20.0.1", "10.0.0.5"),
        ):
            with self.assertRaises(ValueError):
                _validate_provider_base_url("https://openrouter.ai")


if __name__ == "__main__":
    unittest.main()
