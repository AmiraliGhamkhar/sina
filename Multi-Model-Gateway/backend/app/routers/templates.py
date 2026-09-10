from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
import uuid

from app.db import get_db
from app.core.security import get_current_user
from app.models.templates import PromptTemplate 
from app.services.template import rewrite_prompt

router = APIRouter()

class TemplateCreate(BaseModel):
    name: str
    description: str
    structure: str

class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str]= None
    structure: Optional[str]= None


@router.post("/templates")
async def create_template(request: TemplateCreate, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user)):
    template = PromptTemplate(
        id=uuid.uuid4(),
        user_id=user_id,
        name= request.name,
        description= request.description,
        structure= request.structure
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template

@router.get("/templates")
async def list_templates(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    query = select(PromptTemplate).where(PromptTemplate.user_id == user_id)
    if limit is not None:
        query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return {"data": result.scalars().all()}

@router.get("/templates/{template_id}")
async def get_template(template_id: UUID, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user)):
    result = await db.execute(select(PromptTemplate).where(PromptTemplate.id == template_id))
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code= 404, detail= "Template not found")
    if str(template.user_id) != str(user_id):
        raise HTTPException(status_code= 403, detail= "Unauthorised")
    return template

@router.patch("/templates/{template_id}")
async def update_template(template_id: UUID, request: TemplateUpdate, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user)):
    result = await db.execute(select(PromptTemplate).where(PromptTemplate.id == template_id))

    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if str(template.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorised")
    
    for key, value in request.model_dump(exclude_none=True).items():
        setattr(template, key, value)
    
    await db.commit()
    await db.refresh(template)
    return template

@router.delete("/templates/{template_id}")
async def delete_template(template_id: UUID, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user)):
    result = await db.execute(select(PromptTemplate).where(PromptTemplate.id == template_id))

    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if str(template.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorised")
    
    await db.delete(template)
    await db.commit()
    return {"detail": "Template deleted"}



# for image prompt preprocessing
class RewriteRequest(BaseModel):
    prompt: str
    template_id: Optional[str] = None

@router.post("/templates/rewrite")
async def rewrite(request: RewriteRequest, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user)):
    result = await rewrite_prompt(request.prompt, request.template_id, db, user_id)
    return {"rewritten_prompt": result}