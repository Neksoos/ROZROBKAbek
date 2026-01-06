# src/routers/mail.py
from __future__ import annotations

import math
import datetime as dt
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel, Field

from db import get_pool

router = APIRouter(prefix="/api/mail", tags=["mail"])

# ────────────────────────────────────────────────────────────────────
# Схема БД (сумісність rcpt_tg / recipient_tg)
# ────────────────────────────────────────────────────────────────────
async def _ensure_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mail_messages (
                id           BIGSERIAL PRIMARY KEY,
                sender_tg    BIGINT NOT NULL,
                sender_name  TEXT   NOT NULL,
                rcpt_tg      BIGINT,
                rcpt_name    TEXT   NOT NULL,
                body         TEXT   NOT NULL,
                sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                is_read      BOOLEAN NOT NULL DEFAULT FALSE,
                deleted_in   BOOLEAN NOT NULL DEFAULT FALSE,
                deleted_out  BOOLEAN NOT NULL DEFAULT FALSE
            );
            """
        )
        await conn.execute(
            "ALTER TABLE mail_messages ADD COLUMN IF NOT EXISTS recipient_tg BIGINT;"
        )
        await conn.execute(
            "ALTER TABLE mail_messages ADD COLUMN IF NOT EXISTS rcpt_tg BIGINT;"
        )
        # синхронізація колонок
        await conn.execute(
            """
            UPDATE mail_messages
            SET rcpt_tg = recipient_tg
            WHERE rcpt_tg IS NULL AND recipient_tg IS NOT NULL;
            """
        )
        await conn.execute(
            """
            UPDATE mail_messages
            SET recipient_tg = rcpt_tg
            WHERE recipient_tg IS NULL AND rcpt_tg IS NOT NULL;
            """
        )
        # індекси
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mail_rcpt       ON mail_messages(rcpt_tg, sent_at DESC);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mail_rcpt_old   ON mail_messages(recipient_tg, sent_at DESC);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mail_sender     ON mail_messages(sender_tg, sent_at DESC);"
        )


# ────────────────────────────────────────────────────────────────────
# Pydantic моделі
# ────────────────────────────────────────────────────────────────────
class MailListItem(BaseModel):
    id: int
    peer_name: str
    preview: str
    sent_at: dt.datetime
    is_read: Optional[bool] = None  # тільки для inbox


class MailPage(BaseModel):
    items: List[MailListItem]
    page: int
    pages: int
    total: int


class MailMessage(BaseModel):
    id: int
    from_name: Optional[str] = None
    to_name: Optional[str] = None
    body: str
    sent_at: dt.datetime
    is_read: Optional[bool] = None


class SendMailReq(BaseModel):
    from_tg: int = Field(..., description="Відправник (tg_id)")
    to_tg: Optional[int] = Field(
        None, description="Одержувач (tg_id). Якщо нема — шукаємо по to_name"
    )
    to_name: Optional[str] = Field(
        None, description="Ім'я персонажа-одержувача (як у профілі)"
    )
    body: str = Field(..., min_length=1, max_length=2000)


class SendMailResp(BaseModel):
    ok: bool
    id: Optional[int] = None


class SearchPlayer(BaseModel):
    tg_id: int
    name: str


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────
async def _player_name(tg_id: int) -> Optional[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT name FROM players WHERE tg_id=$1", tg_id)
    return str(row["name"]) if row and row["name"] else None


async def _find_players_by_name(name: str) -> List[SearchPlayer]:
    q = (name or "").strip()
    if not q:
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tg_id, name
            FROM players
            WHERE name ILIKE $1
            ORDER BY name
            LIMIT 10
            """,
            f"%{q}%",
        )
    return [SearchPlayer(tg_id=int(r["tg_id"]), name=str(r["name"])) for r in rows]


# ────────────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────────────
@router.get("/inbox", response_model=MailPage)
async def inbox(
    tg_id: int = Query(..., description="Мій tg_id"),
    page: int = Query(0, ge=0),
    page_size: int = Query(7, ge=1, le=50),
):
    await _ensure_schema()
    offset = page * page_size
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = (
            await conn.fetchval(
                """
                SELECT count(*)
                FROM mail_messages
                WHERE COALESCE(rcpt_tg, recipient_tg)=$1
                  AND NOT deleted_in
                """,
                tg_id,
            )
            or 0
        )
        rows = await conn.fetch(
            """
            SELECT id, sender_name, body, sent_at, is_read
            FROM mail_messages
            WHERE COALESCE(rcpt_tg, recipient_tg)=$1
              AND NOT deleted_in
            ORDER BY sent_at DESC
            LIMIT $2 OFFSET $3
            """,
            tg_id,
            page_size,
            offset,
        )
    pages = max(1, math.ceil(total / page_size))
    items = [
        MailListItem(
            id=int(r["id"]),
            peer_name=str(r["sender_name"]),
            preview=(r["body"][:40] + "…")
            if len(r["body"]) > 40
            else str(r["body"]),
            sent_at=r["sent_at"],
            is_read=bool(r["is_read"]),
        )
        for r in rows
    ]
    return MailPage(items=items, page=page, pages=pages, total=total)


@router.get("/outbox", response_model=MailPage)
async def outbox(
    tg_id: int = Query(..., description="Мій tg_id"),
    page: int = Query(0, ge=0),
    page_size: int = Query(7, ge=1, le=50),
):
    await _ensure_schema()
    offset = page * page_size
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = (
            await conn.fetchval(
                """
                SELECT count(*)
                FROM mail_messages
                WHERE sender_tg=$1
                  AND NOT deleted_out
                """,
                tg_id,
            )
            or 0
        )
        rows = await conn.fetch(
            """
            SELECT id, rcpt_name, body, sent_at
            FROM mail_messages
            WHERE sender_tg=$1
              AND NOT deleted_out
            ORDER BY sent_at DESC
            LIMIT $2 OFFSET $3
            """,
            tg_id,
            page_size,
            offset,
        )
    pages = max(1, math.ceil(total / page_size))
    items = [
        MailListItem(
            id=int(r["id"]),
            peer_name=str(r["rcpt_name"]),
            preview=(r["body"][:40] + "…")
            if len(r["body"]) > 40
            else str(r["body"]),
            sent_at=r["sent_at"],
        )
        for r in rows
    ]
    return MailPage(items=items, page=page, pages=pages, total=total)


@router.get("/message/{msg_id}", response_model=MailMessage)
async def view_message(
    msg_id: int,
    box: str = Query(..., regex="^(in|out)$", description="in | out"),
    tg_id: int = Query(..., description="Мій tg_id"),
):
    await _ensure_schema()
    pool = await get_pool()
    async with pool.acquire() as conn:
        if box == "in":
            row = await conn.fetchrow(
                """
                UPDATE mail_messages
                   SET is_read=TRUE
                 WHERE id=$1
                   AND COALESCE(rcpt_tg, recipient_tg)=$2
                   AND NOT deleted_in
                 RETURNING id, sender_name, body, sent_at, is_read
                """,
                msg_id,
                tg_id,
            )
            if not row:
                raise HTTPException(404, "NOT_FOUND")
            return MailMessage(
                id=int(row["id"]),
                from_name=str(row["sender_name"]),
                body=str(row["body"]),
                sent_at=row["sent_at"],
                is_read=bool(row["is_read"]),
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT id, rcpt_name, body, sent_at
                FROM mail_messages
                WHERE id=$1
                  AND sender_tg=$2
                  AND NOT deleted_out
                """,
                msg_id,
                tg_id,
            )
            if not row:
                raise HTTPException(404, "NOT_FOUND")
            return MailMessage(
                id=int(row["id"]),
                to_name=str(row["rcpt_name"]),
                body=str(row["body"]),
                sent_at=row["sent_at"],
            )


@router.post("/send", response_model=SendMailResp)
async def send_mail(req: SendMailReq = Body(...)):
    await _ensure_schema()

    body = (req.body or "").strip()
    if not body:
        raise HTTPException(400, "EMPTY_BODY")
    if len(body) > 2000:
        raise HTTPException(400, "BODY_TOO_LONG")

    # визначаємо отримувача
    rcpt_tg = req.to_tg
    rcpt_name: Optional[str] = None
    if rcpt_tg is None:
        q = (req.to_name or "").strip()
        if not q:
            raise HTTPException(400, "NO_RECIPIENT")
        matches = await _find_players_by_name(q)
        if not matches:
            raise HTTPException(404, "RECIPIENT_NOT_FOUND")
        rcpt_tg = matches[0].tg_id
        rcpt_name = matches[0].name

    if int(rcpt_tg) == int(req.from_tg):
        raise HTTPException(400, "CANNOT_SEND_TO_SELF")

    sender_name = await _player_name(req.from_tg)
    if not sender_name:
        raise HTTPException(404, "SENDER_NOT_FOUND")

    if rcpt_name is None:
        rcpt_name = await _player_name(rcpt_tg)
        if not rcpt_name:
            raise HTTPException(404, "RECIPIENT_NOT_FOUND")

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO mail_messages
              (sender_tg, sender_name, rcpt_tg, recipient_tg, rcpt_name, body)
            VALUES ($1,$2,$3,$3,$4,$5)
            RETURNING id
            """,
            req.from_tg,
            sender_name,
            rcpt_tg,
            rcpt_name,
            body,
        )
    return SendMailResp(ok=True, id=int(row["id"]))


@router.delete("/inbox/{msg_id}")
async def delete_inbox(msg_id: int, tg_id: int = Query(...)):
    await _ensure_schema()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE mail_messages
            SET deleted_in=TRUE
            WHERE id=$1
              AND COALESCE(rcpt_tg, recipient_tg)=$2
            """,
            msg_id,
            tg_id,
        )
    return {"ok": True}


@router.delete("/outbox/{msg_id}")
async def delete_outbox(msg_id: int, tg_id: int = Query(...)):
    await _ensure_schema()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE mail_messages
            SET deleted_out=TRUE
            WHERE id=$1
              AND sender_tg=$2
            """,
            msg_id,
            tg_id,
        )
    return {"ok": True}


@router.get("/search", response_model=List[SearchPlayer])
async def search_players(name: str = Query(..., min_length=2)):
    # пошук адресатів по імені
    return await _find_players_by_name(name)