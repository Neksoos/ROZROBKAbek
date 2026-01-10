from __future__ import annotations

from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.chat import (
    get_history as chat_get_history,
    send_message as chat_send_message,
    get_online as chat_get_online,
)

router = APIRouter(prefix="/chat/tavern", tags=["tavern-chat"])


class ChatMessage(BaseModel):
    id: int
    tg_id: int
    name: str
    text: str
    created_at: float = Field(..., description="Unix time (seconds)")
    system: bool = False


class ChatHistoryResponse(BaseModel):
    ok: bool
    room: str
    messages: List[ChatMessage]
    last_id: int
    online: int


class ChatSendRequest(BaseModel):
    tg_id: Optional[int] = None
    text: str


class ChatSendResponse(BaseModel):
    ok: bool
    message: ChatMessage
    online: int


class OnlineUser(BaseModel):
    tg_id: int
    last_seen: int


class ChatOnlineResponse(BaseModel):
    ok: bool
    room: str
    online: int
    users: List[OnlineUser]


@router.get("/history", response_model=ChatHistoryResponse)
async def get_history(
    tg_id: int = Query(...),
    since_id: int = Query(0),
    after: int = Query(0),
    limit: int = Query(50, ge=1, le=200),
) -> ChatHistoryResponse:
    effective_since = max(int(since_id or 0), int(after or 0))

    msgs, last_id, online = await chat_get_history(
        "tavern",
        tg_id=tg_id,
        since_id=effective_since,
        limit=limit,
        max_messages=2000,
        online_ttl=60,
    )

    return ChatHistoryResponse(
        ok=True,
        room="tavern",
        messages=[
            ChatMessage(
                id=m.id,
                tg_id=m.tg_id,
                name=m.name,
                text=m.text,
                created_at=m.timestamp,
                system=m.system,
            )
            for m in msgs
        ],
        last_id=last_id,
        online=online,
    )


@router.post("/send", response_model=ChatSendResponse)
async def send_message(
    payload: ChatSendRequest,
    tg_id: Optional[int] = Query(None),
) -> ChatSendResponse:
    effective_tg_id = payload.tg_id or tg_id
    if not effective_tg_id:
        raise HTTPException(status_code=422, detail="tg_id is required")

    msg, online = await chat_send_message(
        "tavern",
        tg_id=int(effective_tg_id),
        text=payload.text,
        max_length=300,
        rate_limit=1.5,
        online_ttl=60,
        max_messages=2000,
    )

    return ChatSendResponse(
        ok=True,
        message=ChatMessage(
            id=msg.id,
            tg_id=msg.tg_id,
            name=msg.name,
            text=msg.text,
            created_at=msg.timestamp,
            system=msg.system,
        ),
        online=online,
    )


@router.get("/online", response_model=ChatOnlineResponse)
async def online(
    tg_id: int = Query(...),
    limit: int = Query(30, ge=1, le=100),
) -> ChatOnlineResponse:
    online_count, users = await chat_get_online("tavern", tg_id=tg_id, limit=limit, online_ttl=60)
    return ChatOnlineResponse(
        ok=True,
        room="tavern",
        online=online_count,
        users=[OnlineUser(**u) for u in users],
    )