# routers/referrals.py
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from loguru import logger

# ── DB ───────────────────────────────────────────────────────────────
try:
    from db import get_pool  # type: ignore
except Exception:
    async def get_pool():  # type: ignore
        return None

# ✅ tg_id беремо з initData (X-Init-Data)
from routers.auth import get_tg_id  # type: ignore

router = APIRouter(prefix="/api/referrals", tags=["referrals"])

BOT_USERNAME = os.getenv("BOT_USERNAME")  # для побудови диплінку

# ── helpers ─────────────────────────────────────────────────────────
_DEEPLINK_RE = re.compile(
    r"(?ix)"
    r"(?:^(?:ref|r|u)\s*[:=]?\s*([0-9]{4,})$)"  # ref123 / ref=123 / r123 …
    r"|"
    r"(?:([0-9]{4,})$)"                        # просто «123456»
)


def _parse_inviter(payload: Optional[str]) -> Optional[int]:
    if not payload:
        return None
    m = _DEEPLINK_RE.search(payload.strip())
    if not m:
        return None
    g = m.group(1) or m.group(2)
    try:
        v = int(g)  # type: ignore[arg-type]
        return v if v > 0 else None
    except Exception:
        return None


def _build_link(username: str, inviter_id: int) -> str:
    return f"https://t.me/{username}?start=ref{inviter_id}"


async def _ensure_schema() -> bool:
    pool = await get_pool()
    if not pool:
        return False
    async with pool.acquire() as c:
        await c.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                id           BIGSERIAL PRIMARY KEY,
                invitee_id   BIGINT UNIQUE NOT NULL,
                inviter_id   BIGINT NOT NULL,
                reward_paid  BOOLEAN NOT NULL DEFAULT FALSE,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        await c.execute(
            "CREATE INDEX IF NOT EXISTS idx_ref_inviter ON referrals(inviter_id);"
        )
    return True


async def _stats(uid: int) -> Tuple[int, int]:
    pool = await get_pool()
    if not pool:
        return (0, 0)
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT COUNT(*)::int AS c, "
            "COALESCE(SUM(CASE WHEN reward_paid THEN 1 ELSE 0 END),0)::int AS p "
            "FROM referrals WHERE inviter_id=$1",
            uid,
        )
        return (int(row["c"]), int(row["p"])) if row else (0, 0)


# ── models ──────────────────────────────────────────────────────────
class BindRequest(BaseModel):
    payload: Optional[str] = None
    inviter_id: Optional[int] = None


# ── endpoints ───────────────────────────────────────────────────────
@router.get("/link")
async def get_link(me: int = Depends(get_tg_id)):
    """
    Повертає диплінк рефералки + статистику.
    me береться з initData (X-Init-Data).
    """
    username = BOT_USERNAME or "your_bot"
    link = _build_link(username, me)
    total, paid = await _stats(me)
    return {
        "ok": True,
        "username": username,
        "link": link,
        "stats": {"invited": total, "paid": paid},
    }


@router.post("/bind")
async def bind_ref(req: BindRequest, me: int = Depends(get_tg_id)):
    """
    Прив’язує інвайтера до поточного користувача.
    me береться з initData (X-Init-Data).
    """
    ok_schema = await _ensure_schema()
    if not ok_schema:
        raise HTTPException(500, "DB unavailable")

    inviter = req.inviter_id or _parse_inviter(req.payload)
    if not inviter or inviter == me:
        return {"ok": False, "bound": False, "reason": "invalid_inviter"}

    pool = await get_pool()
    if not pool:
        raise HTTPException(500, "DB unavailable")

    async with pool.acquire() as c:
        exists = await c.fetchval(
            "SELECT 1 FROM referrals WHERE invitee_id=$1 LIMIT 1", me
        )
        if exists:
            return {"ok": True, "bound": False, "reason": "already_linked"}

        await c.execute(
            "INSERT INTO referrals(invitee_id, inviter_id) VALUES ($1,$2)",
            me,
            inviter,
        )

    logger.info(f"[ref] linked invitee={me} <- inviter={inviter}")
    return {"ok": True, "bound": True}


@router.get("/stats")
async def my_stats(me: int = Depends(get_tg_id)):
    """
    Статистика рефералів поточного користувача.
    me береться з initData (X-Init-Data).
    """
    total, paid = await _stats(me)
    return {"ok": True, "invited": total, "paid": paid}


@router.get("")
async def combined(me: int = Depends(get_tg_id)):
    """
    Одним запитом: лінк + статистика для мініапу.
    me береться з initData (X-Init-Data).
    """
    username = BOT_USERNAME or "your_bot"
    link = _build_link(username, me)
    total, paid = await _stats(me)
    return {
        "ok": True,
        "username": username,
        "link": link,
        "stats": {"invited": total, "paid": paid},
    }