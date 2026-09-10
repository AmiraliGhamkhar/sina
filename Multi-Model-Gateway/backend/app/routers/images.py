import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from app.db import get_db
from app.core.security import get_current_user
from app.core.redis import get_redis
from app.core.config import settings
from app.services.template import rewrite_prompt
from app.services.image_security import validate_image_ref
from app.services.comfy import (
    generate_image,
    get_job_status,
    ASPECT_RATIOS,
    DEFAULT_ASPECT_RATIO,
)

router = APIRouter()

class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: Optional[str] = Field(default="text, watermark, blurry, low quality", max_length=4000)
    template_id: Optional[str] = None
    workflow_id: Optional[str] = None
    steps: int = Field(default=10, ge=1, le=50)
    cfg: float = Field(default=1.2, ge=0, le=20)
    aspect_ratio: str = Field(default=DEFAULT_ASPECT_RATIO)
    batch_size: int = Field(default=1, ge=1, le=8)
    seed: Optional[int] = Field(default=None, ge=0)
    rewrite: Optional[bool] = True

    @field_validator("aspect_ratio")
    @classmethod
    def _validate_aspect_ratio(cls, v: str) -> str:
        if v not in ASPECT_RATIOS:
            raise ValueError(
                f"Invalid aspect_ratio '{v}'. Must be one of: {', '.join(ASPECT_RATIOS)}"
            )
        return v

@router.post("/images/generate")
async def generate(request: ImageRequest, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user)):
    prompt = request.prompt
    if request.rewrite:
        prompt = await rewrite_prompt(request.prompt, request.template_id, db, user_id)

    prompt_id = await generate_image(
        request.workflow_id,
        user_id,
        db,
        prompt=prompt,
        negative_prompt=request.negative_prompt,
        steps=request.steps,
        cfg=request.cfg,
        aspect_ratio=request.aspect_ratio,
        batch_size=request.batch_size,
        seed=request.seed,
    )

    # remember who owns this job so status lookups can be authorised
    redis = await get_redis()
    await redis.set(f"imgjob:{prompt_id}", str(user_id), ex=3600)

    payload = {"prompt_id": prompt_id, "rewritten_prompt": prompt}
    return payload

@router.get("/images/status/{prompt_id}")
async def job_status(prompt_id: str, user_id: str = Depends(get_current_user)):
    redis = await get_redis()
    owner = await redis.get(f"imgjob:{prompt_id}")
    if owner != str(user_id):
        # unknown, expired, or someone else's job
        raise HTTPException(status_code=404, detail="job not found")
    try:
        status = await get_job_status(prompt_id)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ComfyUI unavailable")
    # remember who owns each finished image so /images/file can authorise it
    if status.get("status") == "complete":
        for img in status["images"]:
            await redis.set(
                f"imgfile:{img['filename']}",
                str(user_id),
                ex=settings.IMAGE_FILE_TTL_SECONDS,
            )
    return status


@router.get("/images/file")
async def image_file(
    filename: str,
    subfolder: str = "",
    type: str = "output",
    user_id: str = Depends(get_current_user),
):
    # ownership: only the user who generated the image may fetch the file
    redis = await get_redis()
    owner = await redis.get(f"imgfile:{filename}")
    if owner != str(user_id):
        raise HTTPException(status_code=404, detail="image not found")
    try:
        filename, subfolder, type = validate_image_ref(filename, subfolder, type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    params = {"filename": filename}
    if subfolder:
        params["subfolder"] = subfolder
    params["type"] = type
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            resp = await client.get(f"{settings.COMFY_URL}/view", params=params)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="image backend unavailable")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="image unavailable")
    media = resp.headers.get("content-type", "application/octet-stream")
    return Response(content=resp.content, media_type=media)


@router.get("/images/aspect-ratios")
async def list_aspect_ratios(user_id: str = Depends(get_current_user)):
    # Static config & the single source of truth for the
    # ResolutionSelector node, shared by every workflow that uses it.
    return {"aspect_ratios": ASPECT_RATIOS, "default": DEFAULT_ASPECT_RATIO}