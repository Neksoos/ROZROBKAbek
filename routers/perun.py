# routers/perun.py
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel
from loguru import logger

from db import get_pool  # async pool to Postgres

# ✅ PvP state (no telegram)
try:
    from services.pvp import init_duel_state  # type: ignore
except Exception:
    init_duel_state = None  # type: ignore


router = APIRouter(prefix="/api/perun", tags=["perun"])


async def current_tg_id(
    x_tg_id: Optional[int] = Header(default=None, alias="X-Tg-Id"),
    q_tg_id: Optional[int] = Query(default=None, alias="tg_id"),
) -> int:
    uid = x_tg_id or q_tg_id
    if not uid:
        raise HTTPException(
            status_code=401,
            detail="Missing tg id (X-Tg-Id header or ?tg_id=)",
        )
    return int(uid)


async def ensure_perun_schema(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS perun_queue(
            tg_id BIGINT PRIMARY KEY,
            joined_at TIMESTAMP NOT NULL DEFAULT now()
        );
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS perun_queue_joined_idx ON perun_queue(joined_at);"
    )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS perun_duels(
            id BIGSERIAL PRIMARY KEY,
            p1 BIGINT NOT NULL,
            p2 BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP NOT NULL DEFAULT now()
        );
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS perun_duels_status_idx ON perun_duels(status);")
    await conn.execute("CREATE INDEX IF NOT EXISTS perun_duels_created_idx ON perun_duels(created_at);")


class PerunStatusDTO(BaseModel):
    ok: bool = True
    active: int = 0
    online: int = 0
    rating: Optional[int] = None
    place: Optional[int] = None
    season: Optional[str] = None


class SimpleOkDTO(BaseModel):
    ok: bool = True


class LadderRowDTO(BaseModel):
    tg_id: int
    name: str
    level: int
    rating: int
    place: int


class LadderResponseDTO(BaseModel):
    ok: bool = True
    items: List[LadderRowDTO]
    my_place: Optional[int] = None
    my_rating: Optional[int] = None


class QueueMeDTO(BaseModel):
    ok: bool = True
    in_queue: bool = False


class QueueJoinResponseDTO(BaseModel):
    ok: bool = True
    in_queue: bool = True
    matched: bool = False
    duel_id: Optional[int] = None


async def _insert_duel(conn, p1: int, p2: int) -> int:
    duel_id = await conn.fetchval(
        """
        INSERT INTO perun_duels (p1, p2, status, created_at)
        VALUES ($1, $2, 'active', now())
        RETURNING id
        """,
        int(p1), int(p2),
    )
    return int(duel_id)


async def _take_oldest_opponent(conn, me: int) -> Optional[int]:
    """
    ✅ атомарно: DELETE oldest opponent RETURNING tg_id
    """
    row = await conn.fetchrow(
        """
        WITH opp AS (
          DELETE FROM perun_queue
          WHERE ctid IN (
            SELECT ctid
            FROM perun_queue
            WHERE tg_id <> $1
            ORDER BY joined_at ASC
            LIMIT 1
          )
          RETURNING tg_id
        )
        SELECT tg_id FROM opp
        """,
        int(me),
    )
    if not row:
        return None
    return int(row["tg_id"])


@router.get("/queue/me", response_model=QueueMeDTO)
async def perun_queue_me(me: int = Depends(current_tg_id)) -> QueueMeDTO:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_perun_schema(conn)
        exists = await conn.fetchval("SELECT 1 FROM perun_queue WHERE tg_id=$1", me)
        return QueueMeDTO(ok=True, in_queue=bool(exists))


@router.post("/queue/join", response_model=QueueJoinResponseDTO)
async def perun_join_queue(me: int = Depends(current_tg_id)) -> QueueJoinResponseDTO:
    pool = await get_pool()
    if not pool:
        raise HTTPException(status_code=500, detail="DB unavailable")

    duel_id: Optional[int] = None
    opp: Optional[int] = None

    async with pool.acquire() as conn:
        await ensure_perun_schema(conn)

        async with conn.transaction():
            # 1) upsert me
            await conn.execute(
                """
                INSERT INTO perun_queue(tg_id, joined_at)
                VALUES ($1, now())
                ON CONFLICT (tg_id) DO UPDATE SET joined_at = EXCLUDED.joined_at
                """,
                int(me),
            )

            # 2) atomically take opponent
            opp = await _take_oldest_opponent(conn, int(me))
            if not opp:
                return QueueJoinResponseDTO(ok=True, in_queue=True, matched=False, duel_id=None)

            # 3) remove me too
            await conn.execute("DELETE FROM perun_queue WHERE tg_id=$1", int(me))

            # 4) create duel
            duel_id = await _insert_duel(conn, int(opp), int(me))

    # 5) init duel state in Redis (no bot)
    if duel_id is not None and init_duel_state is not None:
        try:
            await init_duel_state(int(duel_id), int(opp), int(me))
        except Exception as e:
            logger.warning(f"perun_join_queue: init_duel_state failed: {e}")

    return QueueJoinResponseDTO(ok=True, in_queue=False, matched=True, duel_id=int(duel_id or 0))


@router.post("/queue/leave", response_model=SimpleOkDTO)
async def perun_leave_queue(me: int = Depends(current_tg_id)) -> SimpleOkDTO:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_perun_schema(conn)
        await conn.execute("DELETE FROM perun_queue WHERE tg_id=$1", int(me))
    return SimpleOkDTO(ok=True)