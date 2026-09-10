"""HttpSandbox — httpx POST to the sandbox service at SANDBOX_URL."""

import httpx

from app.core.config import settings
from app.services.sandbox.protocol import ExecResult


class HttpSandbox:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.SANDBOX_URL).rstrip("/")

    async def exec(self, cmd: str, workdir: str, user_id: str, agent_id: str) -> ExecResult:  # noqa: A003
        try:
            # Shared-secret auth (sweep H3): the sandbox refuses unauthenticated
            # execs, so the client must carry the token on every call.
            headers = {"X-Sandbox-Token": settings.SANDBOX_SHARED_SECRET} if settings.SANDBOX_SHARED_SECRET else {}
            async with httpx.AsyncClient(timeout=settings.SANDBOX_EXEC_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.base_url}/exec",
                    json={"cmd": cmd, "workdir": workdir, "user_id": user_id, "agent_id": agent_id},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                stdout = str(data.get("stdout", ""))[: settings.TOOL_RESULT_MAX_CHARS]
                stderr = str(data.get("stderr", ""))[:1000]
                return ExecResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=int(data.get("exit_code", 0)),
                    truncated=bool(data.get("truncated", False)),
                )
        except httpx.TimeoutException:
            return ExecResult(stdout="", stderr="sandbox timed out", exit_code=124, truncated=False)
        except Exception as e:
            return ExecResult(stdout="", stderr=f"sandbox error: {e}", exit_code=1, truncated=False)
