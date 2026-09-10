from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
import uuid

from app.db import get_db
from app.core.security import get_current_user
from app.models.presets import (
    Preset,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    DEFAULT_TOP_K,
    DEFAULT_MIN_P,
    DEFAULT_REPEAT_PENALTY,
    DEFAULT_CONTEXT_OVERFLOW,
)

router = APIRouter()

class PresetCreate(BaseModel):
    name: str
    system_prompt: Optional[str] = DEFAULT_SYSTEM_PROMPT
    temperature: Optional[float] = DEFAULT_TEMPERATURE
    token_limit: Optional[int] = None
    context_overflow: Optional[str] = DEFAULT_CONTEXT_OVERFLOW
    stop_strings: Optional[list[str]] = None
    top_k: Optional[int] = DEFAULT_TOP_K
    top_p: Optional[float] = DEFAULT_TOP_P
    min_p: Optional[float] = DEFAULT_MIN_P
    repeat_penalty: Optional[float] = DEFAULT_REPEAT_PENALTY

class PresetUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    token_limit: Optional[int] = None
    context_overflow: Optional[str] = None
    stop_strings: Optional[list[str]] = None
    top_k: Optional[int] = None
    top_p: Optional[float] = None
    min_p: Optional[float] = None
    repeat_penalty: Optional[float] = None

@router.post("/presets")
async def create_preset(request: PresetCreate, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user)):
    preset = Preset(
        id=uuid.uuid4(),
        user_id=user_id,
        **request.model_dump()
    )
    db.add(preset)
    await db.commit()
    await db.refresh(preset)
    return preset

@router.get("/presets")
async def list_presets(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    query = select(Preset).where(Preset.user_id == user_id)
    if limit is not None:
        query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return {"data": result.scalars().all()}

@router.get("/presets/{preset_id}")
async def get_preset(preset_id: UUID, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user)):
    result = await db.execute(select(Preset).where(Preset.id == preset_id))
    preset = result.scalar_one_or_none()

    if not preset:
        raise HTTPException(status_code= 404, detail= "Preset not found")
    if str(preset.user_id) != str(user_id):
        raise HTTPException(status_code= 403, detail= "Unauthorised")
    return preset

@router.patch("/presets/{preset_id}")
async def update_preset(preset_id: UUID, request: PresetUpdate, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user)):
    result = await db.execute(select(Preset).where(Preset.id == preset_id))

    preset = result.scalar_one_or_none()
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    if str(preset.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorised")
    
    for key, value in request.model_dump(exclude_none=True).items():
        setattr(preset, key, value)
    
    await db.commit()
    await db.refresh(preset)
    return preset

@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: UUID, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user)):
    result = await db.execute(select(Preset).where(Preset.id == preset_id))

    preset = result.scalar_one_or_none()
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    if str(preset.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorised")
    
    await db.delete(preset)
    await db.commit()
    return {"detail": "Preset deleted"}