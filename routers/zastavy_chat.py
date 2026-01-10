from __future__ import annotations

from typing import List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.chat import (
    get_history as chat_get_history,
    send_message as chat_send_message,
    get_online as chat_get_online,
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
    return VALID_ROLES.get(raw.strip().lower(), "Воїн")


async def _get_member_fort(tg_id: int) -> Optional[Tuple[int, str, str]]:
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
    return int(row["id"]), str(row["name"]), str(row["role"] or "")


class ChatMessage(BaseModel):
    id: int
    tg_id: int
    name: str
    role: str = "Воїн"
    text: str
    created_at: float = Field(..., description="Unix time (seconds)")
    system: bool = False


class ChatHistoryResponse(BaseModel):
    ok: bool
    fort_id: int
    fort_name: str
    messages: List[ChatMessage]
    last_id: int
    online: int


class ChatSendRequest(BaseModel):
    tg_id: Optional[int] = None
    text: str


class ChatSendResponse(BaseModel):
    ok: bool
    online: int


class OnlineUser(BaseModel):
    tg_id: int
    last_seen: int


class ChatOnlineResponse(BaseModel):
    ok: bool
    fort_id: int
    fort_name: str
    online: int
    users: List[OnlineUser]


@router.get("/history", response_model=ChatHistoryResponse)
async def get_history(
    tg_id: int = Query(...),
    since_id: int = Query(0),
    after: int = Query(0),
    limit: int = Query(50, ge=1, le=200),
) -> ChatHistoryResponse:
    fort = await _get_member_fort(tg_id)
    if fort is None:
        raise HTTPException(status_code=404, detail="NOT_IN_FORT")

    fort_id, fort_name, _raw_role = fort
    room = f"fort:{fort_id}"
    effective_since = max(int(since_id or 0), int(after or 0))

    msgs, last_id, online = await chat_get_history(
        room,
        tg_id=tg_id,
        since_id=effective_since,
        limit=limit,
        max_messages=400,
        online_ttl=60,
    )

    out: List[ChatMessage] = []
    for m in msgs:
        role = "Воїн"
        if m.extra and isinstance(m.extra, dict) and "role" in m.extra:
            role = str(m.extra.get("role") or "Воїн")

        out.append(
            ChatMessage(
                id=m.id,
                tg_id=m.tg_id,
                name=m.name,
                role=role,
                text=m.text,
                created_at=float(m.timestamp),
                system=bool(m.system),
            )
        )

    return ChatHistoryResponse(
        ok=True,
        fort_id=fort_id,
        fort_name=fort_name,
        messages=out,
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

    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="EMPTY_TEXT")

    fort = await _get_member_fort(int(effective_tg_id))
    if fort is None:
        raise HTTPException(status_code=404, detail="NOT_IN_FORT")

    fort_id, _fort_name, raw_role = fort
    room = f"fort:{fort_id}"
    role = _normalize_role(raw_role)

    msg, online = await chat_send_message(
        room,
        tg_id=int(effective_tg_id),
        text=text,
        max_length=400,
        rate_limit=3.0,
        extra={"role": role},
        online_ttl=60,
        max_messages=400,
    )

    return ChatSendResponse(ok=True, online=online)


@router.get("/online", response_model=ChatOnlineResponse)
async def online(
    tg_id: int = Query(...),
    limit: int = Query(30, ge=1, le=100),
) -> ChatOnlineResponse:
    fort = await _get_member_fort(tg_id)
    if fort is None:
        raise HTTPException(status_code=404, detail="NOT_IN_FORT")

    fort_id, fort_name, _raw_role = fort
    room = f"fort:{fort_id}"

    online_count, users = await chat_get_online(room, tg_id=tg_id, limit=limit, online_ttl=60)

    return ChatOnlineResponse(
        ok=True,
        fort_id=fort_id,
        fort_name=fort_name,
        online=online_count,
        users=[OnlineUser(**u) for u in users],
    )