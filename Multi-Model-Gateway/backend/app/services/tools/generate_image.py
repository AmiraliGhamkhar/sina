"""First-party tool: generate an image via ComfyUI.

Wraps services/comfy.generate_image + get_job_status. Ownership is registered
in Redis with the same keys the images router uses (imgjob:{prompt_id},
imgfile:{filename}) so the user can poll /v1/images/status and fetch the
result through the authed /v1/images/file route. Generation usually outlives
a single tool call, so the handler polls briefly and, on timeout, reports the
prompt_id instead of failing the agent run.
"""
import asyncio
import json

from app.core.config import settings
from app.core.redis import get_redis
from app.services import comfy
from app.services.tools.registry import Tool, ToolContext, register

DEFAULT_NEGATIVE_PROMPT = "text, watermark, blurry, low quality"
POLL_ATTEMPTS = 4          # 4 × 5s sleeps after the initial check (~20s total)
POLL_SLEEP_SECONDS = 5
JOB_TTL_SECONDS = 3600     # matches the images router's imgjob TTL


async def _generate_image(args: dict, ctx: ToolContext) -> str:
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return "Error: 'prompt' is required"
    if len(prompt) > 4000:
        return "Error: 'prompt' must be at most 4000 characters"

    negative_prompt = args.get("negative_prompt")
    if negative_prompt is None:
        negative_prompt = DEFAULT_NEGATIVE_PROMPT
    negative_prompt = str(negative_prompt)
    if len(negative_prompt) > 4000:
        return "Error: 'negative_prompt' must be at most 4000 characters"

    try:
        steps = int(args.get("steps", 10))
    except (TypeError, ValueError):
        steps = 10
    steps = max(1, min(steps, 50))

    try:
        cfg = float(args.get("cfg", 1.2))
    except (TypeError, ValueError):
        cfg = 1.2
    cfg = max(0.0, min(cfg, 20.0))

    aspect_ratio = args.get("aspect_ratio") or comfy.DEFAULT_ASPECT_RATIO
    if aspect_ratio not in comfy.ASPECT_RATIOS:
        return (
            f"Error: invalid aspect_ratio '{aspect_ratio}'. "
            f"Must be one of: {', '.join(comfy.ASPECT_RATIOS)}"
        )

    seed = args.get("seed")
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            seed = None

    # Generation always uses the base workflow.
    try:
        # One guard for the whole body: Redis acquire, ownership registration,
        # ComfyUI submission, polling and imgfile registration all fail as
        # error strings, never as exceptions — a tool failure must not kill
        # the agent run.
        redis = await get_redis()
        prompt_id = await comfy.generate_image(
            None,
            ctx.user_id,
            ctx.db,
            prompt=prompt,
            negative_prompt=negative_prompt,
            steps=steps,
            cfg=cfg,
            aspect_ratio=aspect_ratio,
            batch_size=1,
            seed=seed,
        )
        await redis.set(f"imgjob:{prompt_id}", str(ctx.user_id), ex=JOB_TTL_SECONDS)

        # one immediate check, then POLL_ATTEMPTS × (sleep + check) ≈ 20s total
        for i in range(POLL_ATTEMPTS + 1):
            status = await comfy.get_job_status(prompt_id)
            if status.get("status") == "complete":
                for img in status["images"]:
                    await redis.set(
                        f"imgfile:{img['filename']}",
                        str(ctx.user_id),
                        ex=settings.IMAGE_FILE_TTL_SECONDS,
                    )
                return json.dumps(
                    {"prompt_id": prompt_id, "images": status["images"]},
                    ensure_ascii=False,
                )
            if status.get("status") == "failed":
                return f"Image generation failed: {status.get('error')}"
            if i < POLL_ATTEMPTS:
                await asyncio.sleep(POLL_SLEEP_SECONDS)
    except Exception as e:  # noqa: BLE001 — tool failures are strings
        return f"Error: image generation failed: {e}"

    return (
        f"Image generation started (prompt_id {prompt_id}) and is still "
        "rendering; check the Images tab shortly."
    )


register(Tool(
    name="generate_image",
    description=(
        "Generate an image with ComfyUI. Returns a JSON object "
        "{prompt_id, images:[{filename, url}]} where url is a same-origin API "
        "path the frontend can render. Use when the user asks to "
        "create/generate/draw an image."
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What to draw (1-4000 characters)",
                "minLength": 1,
                "maxLength": 4000,
            },
            "negative_prompt": {
                "type": "string",
                "description": "Things to avoid in the image",
                "default": DEFAULT_NEGATIVE_PROMPT,
            },
            "steps": {
                "type": "integer",
                "description": "Sampling steps (1-50)",
                "minimum": 1,
                "maximum": 50,
                "default": 10,
            },
            "cfg": {
                "type": "number",
                "description": "CFG scale (0-20)",
                "minimum": 0,
                "maximum": 20,
                "default": 1.2,
            },
            "aspect_ratio": {
                "type": "string",
                "description": f"One of: {', '.join(comfy.ASPECT_RATIOS)}",
            },
            "seed": {
                "type": "integer",
                "description": "Optional seed for reproducible output",
            },
        },
        "required": ["prompt"],
    },
    handler=_generate_image,
))
