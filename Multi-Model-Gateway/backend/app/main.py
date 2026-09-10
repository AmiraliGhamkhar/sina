from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.routers import chat, models, auth, convo, presets, templates, images, workflows, agent, agents, research, hardware, hf, providers
from app.middleware.ratelimit import RateLimitMiddleware
from app.core.redis import get_redis, close_redis
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.metrics_auth import metrics_authorized
from app.core.queue import close_queue
from app.services.mcp_client import mcp_manager
from prometheus_fastapi_instrumentator import Instrumentator


@asynccontextmanager
async def lifespan(app: FastAPI):
    # lifespan (not on_event) so the MCP connections open and close in the
    # same task anyio cancel scopes inside the MCP SDK require that
    await get_redis()           # warm the connection pool
    await mcp_manager.startup() # connect configured MCP servers, register their tools
    yield
    await mcp_manager.shutdown()
    await close_queue()
    await close_redis()


_docs_enabled = settings.ENV != "production"
app = FastAPI(
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
    lifespan=lifespan,
)

# instrument the app but do NOT let the instrumentator register /metrics itself:
# that route is defined below behind a bearer-token gate (S2).
Instrumentator().instrument(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,  # restrict to configured frontends
    allow_credentials=False,                      # bearer tokens go in the Authorization header; no cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RateLimitMiddleware)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    # Translate service domain errors into HTTP at the API boundary.
    # set to match FastAPI's default error shape 
    # covers every AppError subclass (Starlette walks the exception's MRO).
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics(request: Request):
    if not settings.METRICS_TOKEN:
        raise HTTPException(status_code=404, detail="metrics disabled")
    if not metrics_authorized(request.headers.get("Authorization"), settings.METRICS_TOKEN):
        raise HTTPException(status_code=401, detail="unauthorized")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(chat.router, prefix = "/v1")
app.include_router(models.router, prefix = "/v1")
app.include_router(auth.router, prefix="/auth")
app.include_router(convo.router, prefix="/v1")
app.include_router(presets.router, prefix="/v1")
app.include_router(templates.router, prefix="/v1")
app.include_router(images.router, prefix="/v1")
app.include_router(workflows.router, prefix="/v1")
app.include_router(agent.router, prefix="/v1")
app.include_router(agents.router, prefix="/v1")
app.include_router(research.router, prefix="/v1")
app.include_router(hardware.router, prefix="/v1")
app.include_router(hf.router, prefix="/v1")
app.include_router(providers.router, prefix="/v1")
 