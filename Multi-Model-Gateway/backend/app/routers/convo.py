from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status, Depends, Query

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, exists

from app.db import get_db
from app.core import security

from app.models.conversations import Conversation
from app.models.messages import Message

router= APIRouter()

class ConvoCreate(BaseModel):
    title: str

class ConvoRename(BaseModel):
    title: str


# create a new conversation & get it
@router.post("/convo")
async def convo_create(convo_data: ConvoCreate, db: AsyncSession = Depends(get_db), user_id = Depends(security.get_current_user)):
    new_convo= Conversation(title= convo_data.title, user_id= user_id)
    db.add(new_convo)
    await db.commit()
    return {"message": "conversation created", "id": str(new_convo.id)}

@router.get("/convo")
async def convo_get(
    db: AsyncSession = Depends(get_db),
    user_id= Depends(security.get_current_user),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    # list conversations that actually have messages and hide orphans left behind 
    # when a send fails before the model responds.
    query= (
        select(Conversation)
        .where(Conversation.user_id== user_id)
        .where(exists().where(Message.conversation_id == Conversation.id))
        .order_by(Conversation.created_at.desc())
    )
    if limit is not None:
        query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    conversations = result.scalars().all()
    return conversations

# get the messages from a specific conversation
@router.get("/convo/{convo_id}")
async def messages_get(convo_id: str, db: AsyncSession = Depends(get_db), user_id= Depends(security.get_current_user) ):
    # ownership gate only (404 missing / 403 foreign); the messages query
    # below does its own filtering
    await _get_owned_conversation(convo_id, user_id, db)

    # explicit ordering as postgres gives no guarantee without it, and the
    # chat history must render in exchange order
    messages_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == convo_id)
        .order_by(Message.index.asc(), Message.created_at.asc())
    )
    messages = messages_result.scalars().all()
    return messages

# rename the conversation title
@router.patch("/convo/{convo_id}")
async def convo_rename(convo_id: str, convo_data: ConvoRename, db: AsyncSession= Depends(get_db), user_id= Depends(security.get_current_user)):
    conversation = await _get_owned_conversation(convo_id, user_id, db)

    conversation.title= convo_data.title
    await db.commit()
    return {"message": "conversation renamed"}


# remove the conversation from the database
@router.delete("/convo/{convo_id}")
async def convo_delete(convo_id: str, db: AsyncSession= Depends(get_db), user_id= Depends(security.get_current_user)):
    conversation = await _get_owned_conversation(convo_id, user_id, db)

    await db.delete(conversation)
    await db.commit()
    return {"message": "conversation deleted"}


# Message-level operations 

class MessageEdit(BaseModel):
    content: str

class BranchRequest(BaseModel):
    message_id: str


async def _get_owned_conversation(convo_id: str, user_id, db: AsyncSession) -> Conversation:
    query = await db.execute(select(Conversation).where(Conversation.id == convo_id))
    conversation = query.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="convo not found")
    if str(conversation.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="unauthorised user")
    return conversation


async def _get_message(convo_id: str, message_id: str, db: AsyncSession) -> Message:
    query = await db.execute(
        select(Message).where(Message.id == message_id, Message.conversation_id == convo_id)
    )
    message = query.scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")
    return message


# edit a single message's content in place
@router.patch("/convo/{convo_id}/messages/{message_id}")
async def message_edit(convo_id: str, message_id: str, body: MessageEdit, db: AsyncSession = Depends(get_db), user_id= Depends(security.get_current_user)):
    await _get_owned_conversation(convo_id, user_id, db)
    message = await _get_message(convo_id, message_id, db)
    message.content = body.content
    await db.commit()
    return {"message": "message updated"}


# delete a single message
@router.delete("/convo/{convo_id}/messages/{message_id}")
async def message_delete(convo_id: str, message_id: str, db: AsyncSession = Depends(get_db), user_id= Depends(security.get_current_user)):
    await _get_owned_conversation(convo_id, user_id, db)
    message = await _get_message(convo_id, message_id, db)
    await db.delete(message)
    await db.commit()
    return {"message": "message deleted"}


# branch: copy every message up to & including the target into a fresh conversation
@router.post("/convo/{convo_id}/branch")
async def convo_branch(convo_id: str, body: BranchRequest, db: AsyncSession = Depends(get_db), user_id= Depends(security.get_current_user)):
    source = await _get_owned_conversation(convo_id, user_id, db)
    target = await _get_message(convo_id, body.message_id, db)
    cutoff = target.index if target.index is not None else 0

    result = await db.execute(select(Message).where(Message.conversation_id == convo_id))
    to_copy = [m for m in result.scalars().all() if (m.index if m.index is not None else 0) <= cutoff]

    branch = Conversation(
        title=f"{source.title} (branch)",
        user_id=user_id,
        # lineage: which conversation this forked from, and at which message
        parent_id=source.id,
        branched_from_message_id=target.id,
        # carry over the copied turns' usage so the branch doesn't start at 0
        token_count=sum(m.tokens_used or 0 for m in to_copy),
    )
    db.add(branch)
    await db.flush()  # assign branch.id before copying messages into it

    for m in to_copy:
        db.add(Message(
            conversation_id=branch.id,
            role=m.role,
            content=m.content,
            index=m.index,
            model_used=m.model_used,
            tokens_used=m.tokens_used,
        ))
    await db.commit()
    return {"id": str(branch.id)}
