# routers/perun.py
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from loguru import logger
from pydantic import BaseModel

from db import get_pool  # async pool to Postgres

# ✅ PvP state (no telegram)
try:
    from services.pvp import init_duel_state  # type: ignore
except Exception:
    init_duel_state = None  # type: ignore

# ✅ NEW: дуельні дії/стан
from services import pvp  # type: ignore
from services import pvp_stats  # type: ignore


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
    active: int = 0         # активні дуелі
    online: int = 0         # зараз трактуємо як "в черзі"
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


class DuelActiveDTO(BaseModel):
    ok: bool = True
    duel_id: Optional[int] = None


class DuelActionResponseDTO(BaseModel):
    ok: bool = True
    event: Optional[str] = None
    state: Optional[dict] = None
    error: Optional[str] = None


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


async def _get_my_active_duel_id(conn, tg_id: int) -> Optional[int]:
    row = await conn.fetchrow(
        """
        SELECT id
        FROM perun_duels
        WHERE status='active' AND (p1=$1 OR p2=$1)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        int(tg_id),
    )
    return int(row["id"]) if row else None


# ─────────────────────────────────────────────────────────────
# QUEUE
# ─────────────────────────────────────────────────────────────

@router.get("/queue/me", response_model=QueueMeDTO)
async def perun_queue_me(me: int = Depends(current_tg_id)) -> QueueMeDTO:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_perun_schema(conn)
        exists = await conn.fetchval("SELECT 1 FROM perun_queue WHERE tg_id=$1", int(me))
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


# ─────────────────────────────────────────────────────────────
# STATUS + LADDER
# ─────────────────────────────────────────────────────────────

@router.get("/status", response_model=PerunStatusDTO)
async def perun_status(
    scope: str = Query("all", description="day/week/month/all"),
    me: int = Depends(current_tg_id),
) -> PerunStatusDTO:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_perun_schema(conn)

        q_count = int(await conn.fetchval("SELECT COUNT(*) FROM perun_queue"))
        active_duels = int(await conn.fetchval("SELECT COUNT(*) FROM perun_duels WHERE status='active'"))

    rating = None
    place = None
    try:
        r = await pvp_stats.get_rank(scope, int(me))
        if r:
            rating = int(r.get("elo") or 0)
            place = int(r.get("place") or 0)
    except Exception as e:
        logger.warning(f"perun_status: rank failed: {e}")

    return PerunStatusDTO(
        ok=True,
        active=active_duels,
        online=q_count,
        rating=rating,
        place=place,
        season=scope,
    )


@router.get("/ladder", response_model=LadderResponseDTO)
async def perun_ladder(
    scope: str = Query("all", description="day/week/month/all"),
    limit: int = Query(20, ge=1, le=50),
    me: int = Depends(current_tg_id),
) -> LadderResponseDTO:
    items: List[LadderRowDTO] = []
    my_place = None
    my_rating = None

    top_rows = await pvp_stats.get_top(scope, limit=int(limit))
    for i, row in enumerate(top_rows, start=1):
        items.append(
            LadderRowDTO(
                tg_id=int(row["tg_id"]),
                name=str(row["name"]),
                level=int(row["level"]),
                rating=int(row["elo"]),
                place=i,
            )
        )

    try:
        r = await pvp_stats.get_rank(scope, int(me))
        if r:
            my_place = int(r.get("place") or 0)
            my_rating = int(r.get("elo") or 0)
    except Exception as e:
        logger.warning(f"perun_ladder: rank failed: {e}")

    return LadderResponseDTO(ok=True, items=items, my_place=my_place, my_rating=my_rating)


# ─────────────────────────────────────────────────────────────
# DUEL: find active, state, actions
# ─────────────────────────────────────────────────────────────

@router.get("/duel/active", response_model=DuelActiveDTO)
async def perun_duel_active(me: int = Depends(current_tg_id)) -> DuelActiveDTO:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_perun_schema(conn)
        duel_id = await _get_my_active_duel_id(conn, int(me))
    return DuelActiveDTO(ok=True, duel_id=duel_id)


@router.get("/duel/state", response_model=DuelActionResponseDTO)
async def perun_duel_state(
    duel_id: int = Query(...),
    me: int = Depends(current_tg_id),
) -> DuelActionResponseDTO:
    st = await pvp.get_state(int(duel_id))
    if not st:
        return DuelActionResponseDTO(ok=False, error="state_missing")

    if int(me) not in (int(st.get("p1") or 0), int(st.get("p2") or 0)):
        raise HTTPException(status_code=403, detail="not_participant")

    return DuelActionResponseDTO(ok=True, event="state", state=st)


@router.post("/duel/attack", response_model=DuelActionResponseDTO)
async def perun_duel_attack(
    duel_id: int = Query(...),
    me: int = Depends(current_tg_id),
) -> DuelActionResponseDTO:
    res = await pvp.attack(int(me), int(duel_id))
    return DuelActionResponseDTO(
        ok=bool(res.get("ok")),
        event=res.get("event"),
        state=res.get("state"),
        error=res.get("error"),
    )


@router.post("/duel/heal", response_model=DuelActionResponseDTO)
async def perun_duel_heal(
    duel_id: int = Query(...),
    me: int = Depends(current_tg_id),
) -> DuelActionResponseDTO:
    res = await pvp.heal(int(me), int(duel_id))
    return DuelActionResponseDTO(
        ok=bool(res.get("ok")),
        event=res.get("event"),
        state=res.get("state"),
        error=res.get("error"),
    )


@router.post("/duel/surrender", response_model=DuelActionResponseDTO)
async def perun_duel_surrender(
    duel_id: int = Query(...),
    me: int = Depends(current_tg_id),
) -> DuelActionResponseDTO:
    res = await pvp.surrender(int(me), int(duel_id))
    return DuelActionResponseDTO(
        ok=bool(res.get("ok")),
        event=res.get("event"),
        state=res.get("state"),
        error=res.get("error"),
    )