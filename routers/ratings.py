from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel
from loguru import logger

from db import get_pool

# ✅ tg_id тепер береться з initData через core.tg_auth
from core.tg_auth import get_tg_user  # type: ignore

# --- імпорт сервісів (нічна варта / жертва богам / застави) ---

try:
    from services.night_watch import get_week_leaderboard, get_player_rank  # type: ignore
except Exception:
    async def get_week_leaderboard(limit: int = 10):  # type: ignore[override]
        return []

    async def get_player_rank(_tg_id: int):  # type: ignore[override]
        return None


try:
    from services.sacrifice_event import (  # type: ignore
        get_month_leaderboard,
        get_fort_rank_this_month,
    )
except Exception:
    async def get_month_leaderboard(limit: int = 10):  # type: ignore[override]
        return []

    async def get_fort_rank_this_month(_fid: int):  # type: ignore[override]
        return None


try:
    from services.fort_recruit import get_member_fort, get_fort_name  # type: ignore
except Exception:
    async def get_member_fort(_tg_id: int):  # type: ignore[override]
        return None

    async def get_fort_name(_fid: int):  # type: ignore[override]
        return "Невідома застава"


router = APIRouter(prefix="/api/ratings", tags=["ratings"])

# ────────────────────────────────────────────────────────────
# Pydantic-моделі (узгоджені з фронтом /app/ratings/page.tsx)
# ────────────────────────────────────────────────────────────


class CommonRow(BaseModel):
    name: str
    level: int
    xp: int
    chervonci: int


class CommonResp(BaseModel):
    rows: List[CommonRow]


class NightWatchRow(BaseModel):
    place: int
    name: str
    medals: int
    hp_destroyed: int
    kills_total: int


class NightResp(BaseModel):
    top: List[NightWatchRow]
    you: Optional[NightWatchRow] = None


class SacrificeRow(BaseModel):
    place: int
    fort_name: str
    sum: int


class SacrificeResp(BaseModel):
    top: List[SacrificeRow]
    your_fort: Optional[SacrificeRow] = None


class PerunRow(BaseModel):
    place: int
    name: str
    elo: int
    wins: int
    losses: int


class PerunResp(BaseModel):
    scope: str  # "day" | "week" | "month" | "all"
    top: List[PerunRow]
    you: Optional[PerunRow] = None


# ────────────────────────────────────────────────────────────
# Загальний рейтинг (по players)
# ────────────────────────────────────────────────────────────


async def _ensure_players_schema() -> bool:
    """
    Добиваємо, щоб у players були level/xp/coins/chervontsi.
    """
    try:
        pool = await get_pool()
    except Exception:
        return False

    try:
        async with pool.acquire() as conn:
            await conn.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS level INT DEFAULT 1;")
            await conn.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS xp INT DEFAULT 0;")
            await conn.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS coins BIGINT DEFAULT 0;")
            await conn.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS chervontsi BIGINT DEFAULT 0;")
            await conn.execute(
                """
                UPDATE players SET
                  level      = COALESCE(level, 1),
                  xp         = COALESCE(xp, 0),
                  coins      = COALESCE(coins, 0),
                  chervontsi = COALESCE(chervontsi, COALESCE(coins, 0));
                """
            )
        return True
    except Exception as e:
        logger.warning(f"ratings: ensure players schema failed: {e}")
        return False


async def _load_common_top10() -> list[dict]:
    try:
        await _ensure_players_schema()
        pool = await get_pool()
    except Exception:
        return []

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                  COALESCE(name, 'Герой') AS name,
                  COALESCE(level, 1)      AS level,
                  COALESCE(xp, 0)         AS xp,
                  COALESCE(chervontsi, COALESCE(coins,0)) AS chervonci
                FROM players
                ORDER BY level DESC,
                         xp DESC,
                         COALESCE(chervontsi, COALESCE(coins,0)) DESC,
                         name ASC
                LIMIT 10;
                """
            )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"ratings: load_common_top10 failed: {e}")
        return []


@router.get("/common", response_model=CommonResp)
async def ratings_common_top() -> CommonResp:
    rows = await _load_common_top10()
    return CommonResp(rows=[CommonRow(**r) for r in rows])


# ────────────────────────────────────────────────────────────
# Нічна варта
# ────────────────────────────────────────────────────────────


@router.get("/nightwatch", response_model=NightResp)
async def ratings_nightwatch(
    u: dict = Depends(get_tg_user),
) -> NightResp:
    """
    tg_id береться з initData (X-Init-Data).
    """
    tg_id = int(u["id"])

    top_rows_raw = await get_week_leaderboard(limit=10)
    you_raw = await get_player_rank(tg_id)

    top_rows = [NightWatchRow(**r) for r in (top_rows_raw or [])]
    you_row = NightWatchRow(**you_raw) if you_raw else None

    return NightResp(top=top_rows, you=you_row)


# ────────────────────────────────────────────────────────────
# Жертва Богам (місячний рейтинг застав)
# ────────────────────────────────────────────────────────────


@router.get("/sacrifice", response_model=SacrificeResp)
async def ratings_sacrifice(
    u: dict = Depends(get_tg_user),
) -> SacrificeResp:
    """
    tg_id береться з initData (X-Init-Data).
    """
    tg_id = int(u["id"])

    top_raw = await get_month_leaderboard(limit=10)
    top_rows = [SacrificeRow(**r) for r in (top_raw or [])]

    your_fort_row: Optional[SacrificeRow] = None
    try:
        fid = await get_member_fort(tg_id)
        if fid:
            raw = await get_fort_rank_this_month(fid)
            if raw:
                if not raw.get("fort_name"):
                    raw["fort_name"] = await get_fort_name(fid)
                your_fort_row = SacrificeRow(**raw)
    except Exception as e:
        logger.warning(f"ratings: cannot fetch your fort rank: {e}")

    return SacrificeResp(top=top_rows, your_fort=your_fort_row)


# ────────────────────────────────────────────────────────────
# Суд Перуна (PvP ELO)
# ────────────────────────────────────────────────────────────


async def _ensure_perun_elo_schema() -> bool:
    try:
        pool = await get_pool()
    except Exception:
        return False

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS perun_elo(
                    tg_id BIGINT PRIMARY KEY,
                    elo_day   INT DEFAULT 1000,
                    elo_week  INT DEFAULT 1000,
                    elo_month INT DEFAULT 1000,
                    elo_all   INT DEFAULT 1000,
                    wins  INT DEFAULT 0,
                    losses INT DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT now()
                );
                """
            )
        return True
    except Exception as e:
        logger.warning(f"ratings: ensure perun_elo failed: {e}")
        return False


def _scope_to_column(scope: str) -> str:
    return {
        "day": "elo_day",
        "week": "elo_week",
        "month": "elo_month",
        "all": "elo_all",
    }.get(scope, "elo_all")


async def _load_perun_top(scope: str, limit: int = 10) -> list[dict]:
    try:
        await _ensure_perun_elo_schema()
        pool = await get_pool()
    except Exception:
        return []

    col = _scope_to_column(scope)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                WITH ranked AS (
                    SELECT
                        e.tg_id,
                        e.{col} AS elo,
                        e.wins,
                        e.losses,
                        RANK() OVER (
                            ORDER BY e.{col} DESC, e.wins DESC
                        ) AS place
                    FROM perun_elo e
                )
                SELECT
                    r.place,
                    COALESCE(p.name, 'Герой') AS name,
                    r.elo,
                    r.wins,
                    r.losses
                FROM ranked r
                LEFT JOIN players p ON p.tg_id = r.tg_id
                ORDER BY r.place ASC
                LIMIT $1;
                """,
                limit,
            )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"ratings: load perun top failed: {e}")
        return []


async def _load_perun_rank(scope: str, tg_id: int) -> dict | None:
    try:
        await _ensure_perun_elo_schema()
        pool = await get_pool()
    except Exception:
        return None

    col = _scope_to_column(scope)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                WITH ranked AS (
                    SELECT e.tg_id,
                           e.{col} AS elo,
                           e.wins,
                           e.losses,
                           RANK() OVER (
                               ORDER BY e.{col} DESC, e.wins DESC
                           ) AS place
                    FROM perun_elo e
                )
                SELECT r.place,
                       r.elo,
                       r.wins,
                       r.losses,
                       COALESCE(p.name,'Герой') AS name
                FROM ranked r
                LEFT JOIN players p ON p.tg_id = r.tg_id
                WHERE r.tg_id = $1;
                """,
                tg_id,
            )
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"ratings: load perun rank failed: {e}")
        return None


@router.get("/perun", response_model=PerunResp)
async def ratings_perun(
    u: dict = Depends(get_tg_user),
    scope: str = Query(
        "week",
        regex="^(day|week|month|all)$",
        description="Період рейтингу: day|week|month|all",
    ),
) -> PerunResp:
    """
    tg_id береться з initData (X-Init-Data).
    """
    tg_id = int(u["id"])

    if scope not in {"day", "week", "month", "all"}:
        scope = "all"

    top_raw = await _load_perun_top(scope, limit=10)
    you_raw = await _load_perun_rank(scope, tg_id)

    top_rows = [PerunRow(**r) for r in (top_raw or [])]
    you_row = PerunRow(**you_raw) if you_raw else None

    return PerunResp(scope=scope, top=top_rows, you=you_row)