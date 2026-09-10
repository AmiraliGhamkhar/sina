"""First-party tool: current date/time in UTC.

Module is named current_time (not datetime) so it doesn't shadow the stdlib
module. Returns a naive UTC timestamp per the repo convention
(datetime.utcnow), with a note that the user's wall clock may differ.
"""
from datetime import datetime

from app.services.tools.registry import Tool, ToolContext, register

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


async def _current_datetime(args: dict, ctx: ToolContext) -> str:
    now = datetime.utcnow()
    return (
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC ({_WEEKDAYS[now.weekday()]}). "
        "Note: the user's local time may differ from this."
    )


register(Tool(
    name="current_datetime",
    description=(
        "Get the current date and time (UTC). Use when the date, time, or "
        "weekday matters (e.g. relative timing, 'today', scheduling)."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    handler=_current_datetime,
))
