"""
Fort (zastavy) chat router using the generic chat service.

✅ Сумісність:
- send приймає tg_id як у body, так і в query (?tg_id=...)
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.chat import (
    get_history as chat_get_history,
    send_message as chat_send_message,
)

from db import get_pool

router = APIRouter(prefix="/api/zastavy/chat", tags=["zastavy-chat"])

VALID_ROLES = {
    "warrior": "Воїн",
    "voin": "Воїн",
    "воїн": "Воїн",
    "novak": "Воїн",
    "новак": "Воїн",
    "desiatnyk": "Десятник",
    "десятник": "Десятник",
    "sotnyk": "Сотник",
    "сотник": "Сотник",
    "tysyachnyk": "Тисячник",
    "тисячник": "Тисячник",
    "hetman": "Гетьман",
    "гетьман": "Гетьман",
}


def _normalize_role(raw: Optional[str]) -> str:
    if not raw:
        return "Воїн"
    low = raw.strip().lower()
    return VALID_ROLES.get(low, "Воїн")


async def _get_member_fort(tg_id: int) -> Optional[Tuple[int, str, str]]:
    """
    Повернути (fort_id, fort_name, raw_role) для заданого гравця
    або None, якщо він не в заставі.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT f.id, f.name, fm.role
            FROM fort_members fm
            JOIN forts f ON f.id = fm.fort_id
            WHERE fm.tg_id = $1
            """,
            tg_id,
        )

    if not row:
        return None

    fort_id = int(row["id"])
    fort_name = str(row["name"])
    raw_role = row["role"] if row["role"] is not None else ""
    return fort_id, fort_name, str(raw_role)


class ChatMessage(BaseModel):
    tg_id: int
    name: str
    role: str = "Воїн"
    text: str
    ts: int


class ChatHistoryResponse(BaseModel):
    ok: bool
    fort_id: int
    fort_name: str
    messages: List[ChatMessage]


class ChatSendRequest(BaseModel):
    tg_id: Optional[int] = None
    text: str


class ChatSendResponse(BaseModel):
    ok: bool


@router.get("/history", response_model=ChatHistoryResponse)
async def get_history(
    tg_id: int = Query(..., description="Telegram ID гравця"),
) -> ChatHistoryResponse:
    fort = await _get_member_fort(tg_id)
    if fort is None:
        raise HTTPException(status_code=404, detail="NOT_IN_FORT")

    fort_id, fort_name, _role = fort
    room = f"fort:{fort_id}"

    msgs, last_id, online = await chat_get_history(
        room,
        tg_id=tg_id,
        since_id=0,
        limit=200,
        max_messages=200,
    )

    messages: List[ChatMessage] = []
    for m in msgs:
        role = "Воїн"
        if m.extra and isinstance(m.extra, dict) and "role" in m.extra:
            role = str(m.extra.get("role") or "Воїн")

        messages.append(
            ChatMessage(
                tg_id=m.tg_id,
                name=m.name,
                role=role,
                text=m.text,
                ts=int(m.timestamp),
            )
        )

    return ChatHistoryResponse(
        ok=True,
        fort_id=fort_id,
        fort_name=fort_name,
        messages=messages,
    )


@router.post("/send", response_model=ChatSendResponse)
async def send_message(
    payload: ChatSendRequest,
    tg_id: Optional[int] = Query(None, description="Telegram ID гравця (optional)"),
) -> ChatSendResponse:
    effective_tg_id = payload.tg_id or tg_id
    if not effective_tg_id:
        raise HTTPException(status_code=422, detail="tg_id is required")

    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="EMPTY_TEXT")

    fort = await _get_member_fort(int(effective_tg_id))
    if fort is None:
        raise HTTPException(status_code=404, detail="NOT_IN_FORT")

    fort_id, _fort_name, raw_role = fort
    role = _normalize_role(raw_role)
    room = f"fort:{fort_id}"

    await chat_send_message(
        room,
        tg_id=int(effective_tg_id),
        text=text,
        max_length=400,
        rate_limit=3.0,
        extra={"role": role},
        online_ttl=60,
    )

    return ChatSendResponse(ok=True)