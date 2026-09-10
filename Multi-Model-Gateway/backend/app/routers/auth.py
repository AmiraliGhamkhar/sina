import re
import logging
from typing import Optional

from jose import jwt
from datetime import datetime, timedelta
from pydantic import BaseModel, Field, field_validator

from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import get_db
from app.core import security
from app.core.config import settings

from app.models.users import User
from app.models.refresh_tokens import RefreshToken

from app.models.presets import Preset, DEFAULT_TEMPERATURE, DEFAULT_CONTEXT_OVERFLOW
from app.models.templates import PromptTemplate
from app.services.template import DEFAULT_STRUCTURE
from app.services.provider_registry import seed_default_providers
import uuid

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class UserCreate(BaseModel):
    email: str
    password: str

    # validated on registration only — login must keep accepting whatever
    # credentials existing accounts were created with
    @field_validator("email")
    @classmethod
    def _email_format(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.fullmatch(v):
            raise ValueError("invalid email address")
        return v

    @field_validator("password")
    @classmethod
    def _password_length(cls, v: str) -> str:
        if len(v) < 8 or not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("password must be at least 8 characters with at least one letter and one number")
        return v


class UserLogin(BaseModel):
    email: str
    password: str
    device_id: Optional[str] = Field(default=None, max_length=128)


router = APIRouter()

@router.post("/register")
async def register_user(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    if not settings.REGISTRATION_ENABLED:
        raise HTTPException(status_code=403, detail="registration disabled")

    query = await db.execute(select(User).where(User.email == user_data.email))
    user = query.scalar_one_or_none()
    
    if user is not None:
        # Anti-enumeration (S4): respond identically to a successful
        # registration instead of revealing the email is taken.
        return {"message": "user created successfully"}

    # single transaction: user + default preset + default template all land
    # or none do, so a failure can't leave a half init account. The
    # Integrity err catch covers the register register race the select
    # above can't, email is unique in the DB .
    try:
        new_user = User(email=user_data.email, hashed_password=security.hash_password(user_data.password))
        db.add(new_user)
        await db.flush()  # assign new user id; surfaces duplicate email

        # a default model parameter preset for LLMs
        db.add(Preset(
            id=uuid.uuid4(),
            user_id=new_user.id,
            name="Default",
            temperature=DEFAULT_TEMPERATURE,
            context_overflow=DEFAULT_CONTEXT_OVERFLOW,
        ))
        # a default prompt template for t2i ComfyUI tasks
        db.add(PromptTemplate(
            id=uuid.uuid4(),
            user_id=new_user.id,
            name="Default SDXL Template",
            description="Default structure for SDXL prompt rewriting",
            structure=DEFAULT_STRUCTURE,
        ))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning("registration race for %s — email already exists", user_data.email)
        return {"message": "user created successfully"}

    # seed the backward-compat provider rows (Local + OpenRouter, when a key is
    # configured) so a fresh account can chat immediately. Seeding must never
    # fail registration, so any error is logged and swallowed.
    try:
        await seed_default_providers(db, str(new_user.id))
    except Exception as e:  # noqa: BLE001
        logger.warning("provider seeding failed for new user %s: %r", new_user.id, e)

    return {"message": "user created successfully"}
    

@router.post("/login")
async def login_user(user_data: UserLogin, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(User).where(User.email == user_data.email))
    user = query.scalar_one_or_none()

    # run a verification against a dummy hash when the user is absent
    # so login response time doesn't reveal whether the email is registered.
    hashed = user.hashed_password if user is not None else security.DUMMY_PASSWORD_HASH
    password_ok = security.verify_password(user_data.password, hashed)

    if user is not None and password_ok:
        expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRY_DAYS)
        token= security.create_refresh_token(str(user.id), expires_at)
        
        refresh_token= RefreshToken(token_hash= security.hash_token(token),
                                    user_id= user.id,
                                    expires_at= expires_at,
                                    device_id= user_data.device_id
        )
        db.add(refresh_token)
        await db.commit()
        
        access_token = security.create_access_token(str(user.id))
        return {"refresh_token": token,
                "access_token": access_token, 
                "token_type": "bearer"}
    
    raise HTTPException(status_code=401, detail="invalid credentials")


class RefreshRequest(BaseModel):
    refresh_token: str
    device_id: Optional[str] = Field(default=None, max_length=128)


@router.post("/refresh")
async def refresh_token(request: RefreshRequest, db: AsyncSession = Depends(get_db)):
    # find token in database stored as a hash
    token_hash = security.hash_token(request.refresh_token)
    query = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    token_record = query.scalar_one_or_none()

    if token_record is None:
        raise HTTPException(status_code=401, detail="invalid refresh token")

    # reject if the stored record has expired, independent of the JWT's own exp
    if token_record.expires_at is not None and token_record.expires_at < datetime.utcnow():
        await db.delete(token_record)
        await db.commit()
        raise HTTPException(status_code=401, detail="refresh token expired")

    # verify JWT signature/exp and type
    try:
        payload = jwt.decode(request.refresh_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
                             issuer=settings.JWT_ISSUER, audience=settings.JWT_AUDIENCE)
        user_id = payload.get("sub")
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="invalid token type")
        if user_id is None:
            raise HTTPException(status_code=401, detail="invalid token")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="refresh token expired or invalid")

    # defensive cross-check: the DB row and the JWT claim must name the same
    # user, or the rotated token (minted from the JWT claim below) would
    # inherit a tampered/mismatched sub.
    if str(token_record.user_id) != str(payload.get("sub")):
        raise HTTPException(status_code=401, detail="invalid refresh token")

    # device binding: a row minted for one device refuses rotation from any
    # other. Legacy rows (device_id None) accept anything; rows with a binding
    # require an exact match, so a missing device_id counts as a mismatch.
    if token_record.device_id is not None and request.device_id != token_record.device_id:
        raise HTTPException(status_code=401, detail="invalid refresh token")

    # rotate: invalidate the used refresh token and issue a fresh one
    await db.delete(token_record)
    new_expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRY_DAYS)
    new_refresh = security.create_refresh_token(user_id, new_expires_at)
    db.add(RefreshToken(
        token_hash=security.hash_token(new_refresh),
        user_id=user_id,
        expires_at=new_expires_at,
        device_id=token_record.device_id,
    ))
    await db.commit()

    # return new access + rotated refresh token
    access_token = security.create_access_token(user_id)
    return {"refresh_token": new_refresh, "access_token": access_token, "token_type": "bearer"}


@router.post("/logout")
async def logout(request: RefreshRequest, db: AsyncSession = Depends(get_db), user_id: str = Depends(security.get_current_user)):
    query = await db.execute(select(RefreshToken).where(RefreshToken.token_hash== security.hash_token(request.refresh_token), RefreshToken.user_id== user_id))
    token_record = query.scalar_one_or_none()

    if token_record is None:
        raise HTTPException(status_code=401, detail="invalid refresh token")

    await db.delete(token_record)
    await db.commit()
    return {"message": "logged out successfully"}