# services/pvp_stats.py
from __future__ import annotations

from typing import List, Dict, Optional
from loguru import logger

# ✅ ЄДИНИЙ правильний pool
try:
    from db import get_pool  # type: ignore
except Exception:
    get_pool = None  # type: ignore

# ---- Single source of truth for ELO ---------------------------------
try:
    from .perun_elo import ensure_schema, reset_period, top, get_player_elo  # type: ignore
except Exception:
    ensure_schema = None  # type: ignore
    reset_period = None  # type: ignore
    top = None  # type: ignore
    get_player_elo = None  # type: ignore


# =====================================================================
# PUBLIC API (compat layer)
# =====================================================================

async def ensure_schema_compat() -> bool:
    """
    Сумісний ensure_schema для старих місць, що імпортували pvp_stats.ensure_schema.
    """
    if ensure_schema is None:
        return False
    try:
        return bool(await ensure_schema())
    except Exception as e:
        logger.warning(f"pvp_stats.ensure_schema_compat failed: {e}")
        return False


async def record_duel_result(winner_id: int, loser_id: int) -> bool:
    """
    ЗАЛИШЕНО ДЛЯ СУМІСНОСТІ, але тепер НЕ РЕАЛІЗОВУЄ ELO сам.
    В ідеалі результат дуелі має фіксуватися через services/perun_elo.record_duel_result.
    """
    try:
        from .perun_elo import record_duel_result as _rec  # type: ignore
        await _rec(int(winner_id), int(loser_id))
        return True
    except Exception as e:
        logger.warning(f"pvp_stats.record_duel_result failed: {e}")
        return False


# =====================================================================
# RESETS (for cron / scheduler)
# =====================================================================

async def reset_day() -> bool:
    """Щоденний ресет рейтингу «за день» (elo_day -> стартове значення)."""
    if reset_period is None:
        return False
    try:
        await reset_period("day")
        return True
    except Exception as e:
        logger.warning(f"pvp_stats.reset_day failed: {e}")
        return False


async def reset_week() -> bool:
    """Щотижневий ресет рейтингу «за тиждень» (elo_week -> стартове значення)."""
    if reset_period is None:
        return False
    try:
        await reset_period("week")
        return True
    except Exception as e:
        logger.warning(f"pvp_stats.reset_week failed: {e}")
        return False


async def reset_month() -> bool:
    """Щомісячний ресет рейтингу «за місяць» (elo_month -> стартове значення)."""
    if reset_period is None:
        return False
    try:
        await reset_period("month")
        return True
    except Exception as e:
        logger.warning(f"pvp_stats.reset_month failed: {e}")
        return False


# =====================================================================
# LEADERBOARDS / RANKS
# =====================================================================

def _scope_norm(scope: str) -> str:
    scope = (scope or "").strip().lower()
    if scope in ("day", "week", "month", "all"):
        return scope
    return "all"


async def get_top(scope: str, limit: int = 10) -> List[Dict]:
    """
    Повертає топ-N за періодом.
    Використовує perun_elo.top + підтягує імена/рівні з players.
    """
    if not get_pool or top is None:
        return []
    scope = _scope_norm(scope)

    try:
        await ensure_schema_compat()
        rows = await top(scope, limit=limit)  # (tg_id, elo_day, elo_week, elo_month, elo_all, wins)
        tg_ids = [r[0] for r in rows]
        if not tg_ids:
            return []

        pool = await get_pool()
        async with pool.acquire() as conn:
            people = await conn.fetch(
                """
                SELECT tg_id, COALESCE(name,'Герой') AS name, COALESCE(level,1) AS level
                FROM players
                WHERE tg_id = ANY($1)
                """,
                tg_ids,
            )
            mp = {int(p["tg_id"]): {"name": str(p["name"]), "level": int(p["level"])} for p in people}

        out: List[Dict] = []
        for tg_id, elo_day, elo_week, elo_month, elo_all, wins in rows:
            elo = {
                "day": int(elo_day),
                "week": int(elo_week),
                "month": int(elo_month),
                "all": int(elo_all),
            }[scope]

            meta = mp.get(int(tg_id), {"name": "Герой", "level": 1})
            out.append(
                {
                    "tg_id": int(tg_id),
                    "name": meta["name"],
                    "level": int(meta["level"]),
                    "elo": int(elo),
                    "wins": int(wins),
                }
            )
        return out
    except Exception as e:
        logger.warning(f"pvp_stats.get_top({scope}) failed: {e}")
        return []


async def get_rank(scope: str, tg_id: int) -> Optional[Dict]:
    """
    Повертає місце гравця в рейтингу за періодом.
    (Місце рахуємо SQL ранжуванням по perun_elo.)
    """
    if not get_pool:
        return None
    scope = _scope_norm(scope)

    col = {
        "day": "elo_day",
        "week": "elo_week",
        "month": "elo_month",
        "all": "elo_all",
    }[scope]

    try:
        await ensure_schema_compat()
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                WITH ranked AS (
                    SELECT
                        tg_id,
                        {col} AS elo,
                        wins,
                        losses,
                        DENSE_RANK() OVER (ORDER BY {col} DESC, wins DESC, updated_at DESC) AS place
                    FROM perun_elo
                )
                SELECT
                    r.place, r.elo, r.wins, r.losses,
                    COALESCE(p.name,'Герой') AS name,
                    COALESCE(p.level,1) AS level
                FROM ranked r
                LEFT JOIN players p ON p.tg_id = r.tg_id
                WHERE r.tg_id = $1
                """,
                int(tg_id),
            )
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"pvp_stats.get_rank({scope}, {tg_id}) failed: {e}")
        return None


__all__ = [
    "ensure_schema_compat",
    "record_duel_result",
    "reset_day",
    "reset_week",
    "reset_month",
    "get_top",
    "get_rank",
]