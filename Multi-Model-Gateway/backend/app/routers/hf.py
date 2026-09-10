from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import settings
from app.core.security import get_current_user
from app.services.fit_score import build_hf_cookbook, build_hf_model_detail
from app.services.hardware import probe_hardware

router = APIRouter()


@router.get("/hf/models")
async def hf_models(
    search: str = Query(default="", max_length=200),
    limit: int = Query(default=10, ge=1, le=50),
    context_tokens: int = Query(default=None, ge=512, le=262144),
    user_id: str = Depends(get_current_user),
):
    hw = await probe_hardware()
    return await build_hf_cookbook(hw, context_tokens or settings.COOKBOOK_CONTEXT_TOKENS, search, limit)


@router.get("/hf/models/{repo_id:path}")
async def hf_model_detail(
    repo_id: str,
    context_tokens: int = Query(default=None, ge=512, le=262144),
    user_id: str = Depends(get_current_user),
):
    # `{repo_id:path}` — HF repo ids contain a slash ("org/model"). The static
    # /hf/models list route above is matched first by FastAPI, so there is no
    # conflict.
    detail = await build_hf_model_detail(repo_id, context_tokens or settings.COOKBOOK_CONTEXT_TOKENS)
    if detail is None:
        raise HTTPException(status_code=404, detail="model not found or unavailable")
    return detail
