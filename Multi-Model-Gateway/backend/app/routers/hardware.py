from fastapi import APIRouter, Depends, Query

from app.core.config import settings
from app.core.security import get_current_user
from app.services.fit_score import build_cookbook
from app.services.hardware import probe_hardware

router = APIRouter()


@router.get("/hardware")
async def hardware(user_id: str = Depends(get_current_user)):
    return await probe_hardware()


@router.get("/cookbook")
async def cookbook(
    context_tokens: int = Query(default=None, ge=512, le=262144),
    user_id: str = Depends(get_current_user),
):
    hw = await probe_hardware()
    return await build_cookbook(hw, context_tokens or settings.COOKBOOK_CONTEXT_TOKENS)
