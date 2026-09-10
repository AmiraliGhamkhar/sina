import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.core.security import get_current_user
from app.core.stream_guard import acquire_stream_slot
from app.models.tool_permissions import ToolPermission
from app.routers.chat import load_preset
from app.services.agent import run_agent
from app.services.router import ChatRequest
from app.services.tools import registry

router = APIRouter()


# agentic counterpart of /chat/completions:
# has the same request body, but the
# response is a structured SSE stream (see services/agent.py docstring) 
# FUTURE FRONTEND FIX: the plain token parser in api-client.ts cannot read this route
@router.post("/agent/chat")
async def agent_chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    preset = await load_preset(request.preset_id, user_id, db)
    await acquire_stream_slot(user_id)
    return StreamingResponse(
        run_agent(request, user_id, preset, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# every registered tool plus this user's effective permission
@router.get("/agent/tools")
async def list_tools(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    result = await db.execute(
        select(ToolPermission).where(ToolPermission.user_id == user_id)
    )
    overrides = {row.tool_name: row.allowed for row in result.scalars().all()}
    return {
        "data": [
            {
                "name": t.name,
                "description": t.description,
                "first_party": t.first_party,
                "allowed": overrides.get(t.name, t.first_party),
            }
            for t in registry.all_tools()
        ]
    }


class PermissionUpdate(BaseModel):
    allowed: bool


@router.put("/agent/tools/{tool_name}/permission")
async def set_tool_permission(
    tool_name: str,
    body: PermissionUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    if registry.get_tool(tool_name) is None:
        raise HTTPException(status_code=404, detail="tool not found")

    result = await db.execute(
        select(ToolPermission).where(
            ToolPermission.user_id == user_id,
            ToolPermission.tool_name == tool_name,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ToolPermission(id=uuid.uuid4(), user_id=user_id,
                             tool_name=tool_name, allowed=body.allowed)
        db.add(row)
    else:
        row.allowed = body.allowed
    await db.commit()
    return {"name": tool_name, "allowed": body.allowed}
