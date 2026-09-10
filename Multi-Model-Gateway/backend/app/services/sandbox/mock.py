"""MockSandbox — deterministic, no Docker, for tests and ENABLE_CODE_EXECUTION=false dev."""

from app.core.config import settings
from app.services.sandbox.protocol import ExecResult


class MockSandbox:
    """In-memory fake that echoes the command. Never touches the filesystem."""

    async def exec(self, cmd: str, workdir: str, user_id: str, agent_id: str) -> ExecResult:  # noqa: A003
        if "fail" in cmd.lower():
            return ExecResult(stdout="", stderr="mock failure", exit_code=1)
        out = f"mock: {cmd} @ {workdir}"
        if len(out) > settings.TOOL_RESULT_MAX_CHARS:
            return ExecResult(
                stdout=out[: settings.TOOL_RESULT_MAX_CHARS], stderr="", exit_code=0, truncated=True
            )
        return ExecResult(stdout=out, stderr="", exit_code=0)
