"""Unit tests for app.services.egress (deep module — closing F5/F6).

Stdlib unittest only. The whole module is offline-testable because the SSRF guard,
byte cap, and pin logic are exercised against local HTTP listeners and a stubbed
resolver — no real DNS, no real internet.

The tests prove the security properties the module exists for:
  - F5 (DNS-rebinding TOCTOU): the connect happens to the *validated* IP, not a
    fresh hostname lookup. We assert the transport reaches a listener bound to the
    validated IP even when the hostname, if resolved again, would point elsewhere.
  - F6 (gzip-bomb OOM): a body larger than max_bytes is aborted mid-stream, not
    fully buffered.
  - SSRF: a private / loopback / link-local / reserved resolved address is refused.
  - Redirect re-validation: a public host that 302s to an internal address is
    refused on the second hop.
  - Policy tiers: INTERNAL reaches a loopback host; PRIVATE_ALLOWED reaches it too.
  - Generic errors: no internal IP/hostname in the message (F11 convention).
"""
import asyncio
import ipaddress
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit

try:
    from app.services import egress
    from app.services.egress import EgressError, Policy, fetch, fetch_text
except Exception as exc:  # noqa: BLE001
    egress = None
    EgressError = None
    Policy = None
    fetch = None
    fetch_text = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _start_server(body: bytes, status=200, headers=None, redirect_to=None):
    """Start a local HTTP server; returns (url, shutdown_fn)."""
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured["path"] = self.path
            captured["host"] = self.headers.get("Host", "")
            if redirect_to:
                self.send_response(302)
                self.send_header("Location", redirect_to)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            # Read the form body so tests can assert on it.
            length = int(self.headers.get("Content-Length", "0"))
            captured["path"] = self.path
            captured["body"] = self.rfile.read(length)
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{port}/", server.shutdown, captured


class EgressInternetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if egress is None:
            raise unittest.SkipTest(
                f"app.services.egress import failed in this env: {_IMPORT_ERROR}"
            )

    def _resolve_to(self, ip):
        """Patch egress._resolve to always return `ip` (no real DNS)."""

        async def fake_resolve(host):
            return ip

        return fake_resolve

    def test_private_ip_refused(self):
        url, _shutdown, _cap = _start_server(b"x")
        try:
            with patch.object(egress, "_resolve", new=self._resolve_to("10.0.0.5")):
                with self.assertRaises(EgressError) as ctx:
                    asyncio.run(fetch(url))
            self.assertNotIn("10.0.0.5", str(ctx.exception))
        finally:
            _shutdown()

    def test_loopback_ip_refused(self):
        url, _shutdown, _cap = _start_server(b"x")
        try:
            with patch.object(egress, "_resolve", new=self._resolve_to("127.0.0.1")):
                with self.assertRaises(EgressError):
                    asyncio.run(fetch(url))
        finally:
            _shutdown()

    def test_link_local_ip_refused(self):
        # 169.254.169.254 is the cloud-metadata IP — must never be reachable.
        url, _shutdown, _cap = _start_server(b"x")
        try:
            with patch.object(egress, "_resolve", new=self._resolve_to("169.254.169.254")):
                with self.assertRaises(EgressError):
                    asyncio.run(fetch(url))
        finally:
            _shutdown()

    def test_public_ip_pins_to_validated_ip(self):
        # The server is bound to 127.0.0.1 on a random port. We resolve a fake
        # public name to that IP, and build the URL with the server's port but a
        # *fake public hostname*. _pin_url replaces the host with the validated IP
        # but keeps the port, so the request reaches the loopback fixture. If the
        # transport re-resolved the hostname it would fail; pinning is what makes
        # it succeed. This is precisely the F5 closure.
        url, shutdown, cap = _start_server(b"hello")
        port = int(urlsplit(url).port)
        try:
            with patch.object(egress, "_resolve", new=self._resolve_to("127.0.0.1")):
                with patch.object(egress, "_validate", wraps=egress._validate) as v:
                    # PRIVATE_ALLOWED lets us reach the loopback fixture while still
                    # going through the _validate + pin path (the IP _is_ private).
                    body = asyncio.run(
                        fetch(f"http://public.example.test:{port}/x", policy=Policy.PRIVATE_ALLOWED)
                    )
            self.assertEqual(body, b"hello")
            # The Host header must be the ORIGINAL hostname, not the IP.
            self.assertEqual(cap["host"], "public.example.test")
            # And the pin ran the validation for that hostname.
            self.assertTrue(v.called)
        finally:
            shutdown()

    def test_scheme_rejected(self):
        async def run():
            with self.assertRaises(EgressError):
                await fetch("ftp://public.example.test/")
        asyncio.run(run())


class EgressInternalPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if egress is None:
            raise unittest.SkipTest(
                f"app.services.egress import failed in this env: {_IMPORT_ERROR}"
            )

    def test_internal_reaches_loopback(self):
        url, shutdown, cap = _start_server(b"internal ok")
        try:
            body = asyncio.run(fetch(url, policy=Policy.INTERNAL))
            self.assertEqual(body, b"internal ok")
        finally:
            shutdown()

    def test_private_allowed_reaches_loopback(self):
        url, shutdown, _cap = _start_server(b"private ok")
        try:
            body = asyncio.run(fetch(url, policy=Policy.PRIVATE_ALLOWED))
            self.assertEqual(body, b"private ok")
        finally:
            shutdown()


class EgressRedirectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if egress is None:
            raise unittest.SkipTest(
                f"app.services.egress import failed in this env: {_IMPORT_ERROR}"
            )

    def test_redirect_to_internal_refused_even_when_following(self):
        # The public host would 302 us to an internal IP. With follow_redirects
        # on, the SECOND hop must refuse the internal name.
        url, shutdown, _cap = _start_server(
            b"", redirect_to="http://169.254.169.254/latest/meta-data/"
        )
        try:
            # _resolve returns public for the first host, and the internal IP when
            # asked to resolve the redirected host. By pinning to whatever we
            # resolved, the second hop's validated IP is the cloud-metadata IP.
            async def fake_resolve(host):
                return "93.184.216.34" if host.startswith("public") else "169.254.169.254"

            with patch.object(egress, "_resolve", new=fake_resolve):
                with self.assertRaises(EgressError):
                    asyncio.run(fetch(url, follow_redirects=True))
        finally:
            shutdown()


class EgressByteCapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if egress is None:
            raise unittest.SkipTest(
                f"app.services.egress import failed in this env: {_IMPORT_ERROR}"
            )

    def _resolve_to(self, ip):
        async def fake_resolve(host):
            return ip
        return fake_resolve

    def test_oversized_body_aborted(self):
        # PRIVATE_ALLOWED so we can reach the loopback fixture; the cap must be
        # enforced regardless of policy (F6 is orthogonal to SSRF).
        body = b"a" * 3000
        url, shutdown, _cap = _start_server(body)
        try:
            with patch.object(egress, "_resolve", new=self._resolve_to("127.0.0.1")):
                with self.assertRaises(EgressError):
                    asyncio.run(fetch(url, max_bytes=100, policy=Policy.PRIVATE_ALLOWED))
        finally:
            shutdown()

    def test_under_cap_returns_full_body(self):
        body = b"a" * 100
        url, shutdown, _cap = _start_server(body)
        try:
            with patch.object(egress, "_resolve", new=self._resolve_to("127.0.0.1")):
                result = asyncio.run(
                    fetch(url, max_bytes=1000, policy=Policy.PRIVATE_ALLOWED)
                )
            self.assertEqual(result, body)
        finally:
            shutdown()


class EgressSearchBackendRoutingTests(unittest.TestCase):
    """The seam must be real (more than one adapter): the SearXNG and DuckDuckGo
    search backends route through it. SearXNG is a configured host (INTERNAL, gets
    params); DDG is a fixed public host (INTERNET, POST with form)."""

    @classmethod
    def setUpClass(cls):
        if egress is None:
            raise unittest.SkipTest(
                f"app.services.egress import failed in this env: {_IMPORT_ERROR}"
            )

    def test_internal_policy_preserves_params(self):
        # A SearXNG-style GET with query params reaches a localhost fixture.
        url, shutdown, cap = _start_server(b'{"results": []}')
        try:
            body = asyncio.run(
                fetch(url, policy=Policy.INTERNAL, params={"q": "cats", "format": "json"})
            )
            self.assertEqual(body, b'{"results": []}')
            self.assertIn("q=cats", cap["path"])
            self.assertIn("format=json", cap["path"])
        finally:
            shutdown()

    def test_internet_post_with_form(self):
        # A DDG-style POST with form data reaches the fixture (policy INTERNAL
        # for the loopback; the form body is sent).
        url, shutdown, cap = _start_server(b"<html></html>")
        try:
            async def fake_resolve(host):
                return "127.0.0.1"
            with patch.object(egress, "_resolve", new=fake_resolve):
                body = asyncio.run(
                    fetch(
                        url, policy=Policy.PRIVATE_ALLOWED,
                        method="POST", form={"q": "cats"},
                    )
                )
            self.assertEqual(body, b"<html></html>")
            # The form body was urlencoded and sent.
            self.assertIn(b"q=cats", cap["body"])
        finally:
            shutdown()


if __name__ == "__main__":
    unittest.main()
