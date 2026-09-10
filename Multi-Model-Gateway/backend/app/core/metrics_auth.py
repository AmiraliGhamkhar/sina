"""The GET /metrics bearer-token gate, as a pure stdlib-only function.

Kept out of app.main so the offline unit-test suite can import it without a
full settings/secret environment. The empty-token case is the route's job (it
404s before this helper is consulted), so a missing header is simply False.
"""


def metrics_authorized(authorization_header: str | None, token: str) -> bool:
    """The Authorization header must be exactly `Bearer <token>`."""
    if not token:
        return False
    return authorization_header == f"Bearer {token}"
