"""
Tavern chat router using the generic chat service.

Цей модуль визначає HTTP-роутер для публічного чату таверни. Він
делегує всі операції зберігання, читання історії, анти-флуд та онлайн-статистики
у сервіс `services.chat`.

✅ Сумісність:
- history приймає і since_id, і after (аліас для старого фронта)
- send приймає tg_id як у body, так і в query (?tg_id=...)
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.chat import (
    get_history as chat_get_history,
    send_message as chat_send_message,
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
    # tg_id optional — бо може прийти з query
    tg_id: Optional[int] = None
    text: str


class ChatSendResponse(BaseModel):
    ok: bool
    message: ChatMessage
    online: int


@router.get("/history", response_model=ChatHistoryResponse)
async def get_history(
    tg_id: int = Query(..., description="Telegram user id"),
    since_id: int = Query(0, description="Return messages with id > since_id"),
    # ✅ alias для старого фронта
    after: int = Query(0, description="Alias for since_id"),
    limit: int = Query(50, ge=1, le=200),
) -> ChatHistoryResponse:
    effective_since = max(int(since_id or 0), int(after or 0))

    msgs, last_id, online = await chat_get_history(
        "tavern",
        tg_id=tg_id,
        since_id=effective_since,
        limit=limit,
        max_messages=2000,
    )

    messages: List[ChatMessage] = [
        ChatMessage(
            id=m.id,
            tg_id=m.tg_id,
            name=m.name,
            text=m.text,
            created_at=m.timestamp,
            system=m.system,
        )
        for m in msgs
    ]

    return ChatHistoryResponse(
        ok=True,
        room="tavern",
        messages=messages,
        last_id=last_id,
        online=online,
    )


@router.post("/send", response_model=ChatSendResponse)
async def send_message(
    payload: ChatSendRequest,
    tg_id: Optional[int] = Query(None, description="Telegram user id (optional)"),
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
    )

    api_msg = ChatMessage(
        id=msg.id,
        tg_id=msg.tg_id,
        name=msg.name,
        text=msg.text,
        created_at=msg.timestamp,
        system=msg.system,
    )

    return ChatSendResponse(ok=True, message=api_msg, online=online)