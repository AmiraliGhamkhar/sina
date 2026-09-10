from dataclasses import dataclass
from typing import Protocol


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    truncated: bool = False


class Sandbox(Protocol):
    async def exec(self, cmd: str, workdir: str, user_id: str, agent_id: str) -> ExecResult:  # noqa: A003
        ...
