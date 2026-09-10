from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from jose import jwt, JWTError
from app.core.config import settings
from app.core.redis import get_redis
from app.core.trusted_proxies import resolve_client_ip
import logging
import time
import uuid

logger = logging.getLogger(__name__)

# skip rate limiting entirely.
EXCLUDED_PATHS = {"/health", "/metrics", "/docs", "/openapi.json"}

# Unauthenticated auth endpoints have a stricter, IP-based limit to curb
# brute force / credential stuffing / mass account creation.
AUTH_PATHS = {"/auth/login", "/auth/register", "/auth/refresh"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in EXCLUDED_PATHS:
            return await call_next(request)

        user_id = self._extract_user_id(request)

        # Authenticated per-user bucket. Anonymous per-IP bucket so
        # unauthenticated traffic can't bypass the limiter entirely.
        if user_id:
            key = f"rate:user:{user_id}"
            limit = settings.RATE_LIMIT_PER_MINUTE
        else:
            client_ip = self._client_ip(request)
            if path in AUTH_PATHS:
                key = f"rate:auth:{client_ip}"
                limit = settings.AUTH_RATE_LIMIT_PER_MINUTE
            else:
                key = f"rate:ip:{client_ip}"
                limit = settings.RATE_LIMIT_PER_MINUTE

        window = settings.RATE_LIMIT_WINDOW_SECONDS
        now = int(time.time())
        window_start = now - window
        # Unique member so multiple requests within the same second are each counted.
        member = f"{now}:{uuid.uuid4().hex}"

        # a Redis outage degrades to no rate limiting instead of
        # turning every request in the app into a 500.
        try:
            redis = await get_redis()
            pipe = redis.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)   # drop requests outside the window
            pipe.zadd(key, {member: now})                 # record this request
            pipe.zcard(key)                               # count requests in the window
            pipe.expire(key, window)                      # auto-expire idle keys
            results = await pipe.execute()
        except Exception as e:
            logger.warning("rate limiter unavailable (%r); allowing request", e)
            return await call_next(request)

        count = results[2]

        if count > limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={"Retry-After": str(window)},
            )

        return await call_next(request)

    def _extract_user_id(self, request: Request) -> str | None:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        token = auth_header.split(" ", 1)[1]
        try:
            decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            if decoded.get("type") != "access":
                return None
            return decoded.get("sub")
        except JWTError:
            return None

    def _client_ip(self, request: Request) -> str:
        # Trust X-Forwarded-For only when the direct peer is a known reverse
        # proxy (Caddy); otherwise the direct peer IP is used as-is.
        return resolve_client_ip(
            request.client.host if request.client else None,
            request.headers.get("X-Forwarded-For"),
            settings.trusted_proxy_networks,
        )
