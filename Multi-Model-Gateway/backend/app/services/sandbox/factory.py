from app.core.config import settings
from app.services.sandbox.http import HttpSandbox
from app.services.sandbox.mock import MockSandbox


def get_sandbox():
    """Return the sandbox backend for this process.

    Mock when code execution is disabled or no URL is configured — safe for
    tests and local dev without Docker.
    """
    if not settings.ENABLE_CODE_EXECUTION or not settings.SANDBOX_URL:
        return MockSandbox()
    return HttpSandbox()
