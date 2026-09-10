from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
import uuid

from app.db import get_db
from app.core.security import get_current_user
from app.models.workflows import Workflow
from app.services.comfy import validate_workflow_anchors

router = APIRouter()


class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    graph: dict
    param_map: Optional[dict] = None


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    graph: Optional[dict] = None
    param_map: Optional[dict] = None


def _validate_graph(graph: dict):
    """Confirm it's a ComfyUI *API-format* graph: node_id to {class_type, inputs}.

    Catches the mistake of pasting the default UI format export,
    which has {"nodes": [...], "links": [...]} rather than a node map.
    """
    if not isinstance(graph, dict) or not graph:
        raise HTTPException(
            status_code=422,
            detail="graph must be a non-empty ComfyUI API-format object",
        )
    for node_id, node in graph.items():
        if not isinstance(node, dict) or "class_type" not in node:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"node '{node_id}' is missing 'class_type' — export the workflow "
                    "via ComfyUI's 'Save (API Format)'"
                ),
            )


@router.post("/workflows")
async def create_workflow(
    request: WorkflowCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    _validate_graph(request.graph)
    try:
        # R5: critical anchors must be unambiguous unless param_map pins them
        validate_workflow_anchors(request.graph, request.param_map)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    workflow = Workflow(
        id=uuid.uuid4(),
        user_id=user_id,
        name=request.name,
        description=request.description,
        graph=request.graph,
        param_map=request.param_map,
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    return workflow


@router.get("/workflows")
async def list_workflows(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    result = await db.execute(select(Workflow).where(Workflow.user_id == user_id))
    return {"data": result.scalars().all()}


@router.get("/workflows/{workflow_id}")
async def get_workflow_by_id(
    workflow_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if str(workflow.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorised")
    return workflow


@router.patch("/workflows/{workflow_id}")
async def update_workflow(
    workflow_id: UUID,
    request: WorkflowUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if str(workflow.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorised")

    data = request.model_dump(exclude_none=True)
    if "graph" in data:
        _validate_graph(data["graph"])
        try:
            # R5: same anchor check as create — an update can't make a graph
            # ambiguous either. Validate against the EFFECTIVE post-update
            # param_map: a graph-only update keeps the stored param_map, which
            # may be what pins the (now ambiguous) anchors.
            effective_param_map = data.get("param_map", workflow.param_map)
            validate_workflow_anchors(data["graph"], effective_param_map)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    for key, value in data.items():
        setattr(workflow, key, value)

    await db.commit()
    await db.refresh(workflow)
    return workflow


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(
    workflow_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if str(workflow.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorised")

    await db.delete(workflow)
    await db.commit()
    return {"detail": "Workflow deleted"}
