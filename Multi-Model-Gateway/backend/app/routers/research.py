import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.core.queue import get_queue
from app.core.redis import get_redis
from app.core.security import get_current_user
from app.core.stream_guard import acquire_stream_slot, release_stream_slot
from app.models.research_jobs import ResearchJob
from app.services.research import CANCEL_KEY, CHANNEL, resolve_research_model
from app.services.router import Provider

router = APIRouter()


class ResearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    provider: Provider = Provider.auto
    model: str = "auto"


def _job_summary(job: ResearchJob) -> dict:
    return {
        "id": str(job.id),
        "query": job.query,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "created_at": job.created_at,
    }


async def _get_owned_job(job_id: str, user_id: str, db: AsyncSession) -> ResearchJob:
    result = await db.execute(select(ResearchJob).where(ResearchJob.id == job_id))
    job = result.scalar_one_or_none()
    # 404 for both missing and non owned, so job ids canyt be enumerated
    if job is None or str(job.user_id) != str(user_id):
        raise HTTPException(status_code=404, detail="research job not found")
    return job


@router.post("/research")
async def create_research(
    request: ResearchRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    # idempotency: if the same query is already queued/running for this user, return
    # that job instead of enqueuing a duplicate multi-minute run (issues.md CR-13)
    existing = (await db.execute(
        select(ResearchJob)
        .where(
            ResearchJob.user_id == user_id,
            ResearchJob.query == request.query,
            ResearchJob.status.in_(("queued", "running")),
        )
        .limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        return {"job_id": str(existing.id), "status": existing.status}

    # R4: resolve the concrete role + model at submit time. Research can't
    # silently fall back to a rewrite model mid-run — with no capable chat
    # model configured the job is rejected before anything is enqueued.
    resolved = await resolve_research_model(request.provider.value, user_id, db)
    if resolved is None:
        raise HTTPException(status_code=503, detail="no capable chat model configured for research")
    resolved_role, resolved_model = resolved

    job = ResearchJob(
        id=uuid.uuid4(),
        user_id=user_id,
        query=request.query,
        provider=resolved_role,
        model=resolved_model,
        status="queued",
        progress=0,
    )
    db.add(job)
    await db.commit()

    queue = await get_queue()
    await queue.enqueue_job("run_research", str(job.id))
    return {"job_id": str(job.id), "status": "queued"}


@router.get("/research")
async def list_research(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    result = await db.execute(
        select(ResearchJob)
        .where(ResearchJob.user_id == user_id)
        .order_by(ResearchJob.created_at.desc())
        .limit(50)
    )
    return {"data": [_job_summary(j) for j in result.scalars().all()]}


@router.get("/research/{job_id}")
async def get_research(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    job = await _get_owned_job(job_id, user_id, db)
    return {**_job_summary(job), "result": job.result, "sources": job.sources, "error": job.error}


@router.post("/research/{job_id}/cancel")
async def cancel_research(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    job = await _get_owned_job(job_id, user_id, db)
    if job.status in ("complete", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"job already {job.status}")

    redis = await get_redis()
    # the worker checks this flag between steps; expire it in case the job is gone
    await redis.set(CANCEL_KEY.format(job_id=job_id), "1", ex=3600)

    if job.status == "queued":
        # not picked up yet then cancel directly so here worker skips non queued jobs
        job.status = "cancelled"
        await db.commit()
        await redis.publish(CHANNEL.format(job_id=job_id),
                            json.dumps({"type": "done", "status": "cancelled"}))
    return {"id": job_id, "status": job.status}


@router.get("/research/{job_id}/stream")
async def stream_research(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    await acquire_stream_slot(user_id)
    # The only setup between the slot
    # acquire and the StreamingResponse is this ownership check (an await that
    # can raise on DB errors or task cancellation). The redis/pubsub wiring
    # lives inside the relay generator, whose finally already releases the slot
    # — but a failure here happens before the stream exists, so release it on
    # the spot to keep acquire/release 1:1.
    try:
        job = await _get_owned_job(job_id, user_id, db)
    except BaseException:
        await release_stream_slot(user_id)
        raise

    async def relay():
        try:
            async for event in _relay_inner():
                yield event
        finally:
            # release the stream slot on every exit path — normal completion,
            # terminal-job shortcut, error, and client disconnect
            await release_stream_slot(user_id)

    async def _relay_inner():
        # snapshot first, so late subscribers see the current state immediately
        yield f"data: {json.dumps({'type': 'progress', 'stage': job.stage, 'progress': job.progress, 'message': job.status})}\n\n"
        if job.status in ("complete", "failed", "cancelled"):
            if job.status == "complete":
                yield f"data: {json.dumps({'type': 'done', 'status': 'complete', 'result': job.result, 'sources': job.sources}, ensure_ascii=False)}\n\n"
            elif job.status == "failed":
                yield f"data: {json.dumps({'type': 'error', 'message': job.error or 'failed'})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'done', 'status': 'cancelled'})}\n\n"
            return

        redis = await get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(CHANNEL.format(job_id=job_id))
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if message is None:
                    yield ": keepalive\n\n"  # SSE comment keeps proxies from closing the idle stream
                    continue
                data = message["data"]
                yield f"data: {data}\n\n"
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if event.get("type") in ("done", "error"):
                    return
        finally:
            await pubsub.unsubscribe(CHANNEL.format(job_id=job_id))
            await pubsub.aclose()

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
