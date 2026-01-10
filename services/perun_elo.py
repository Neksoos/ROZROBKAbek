# services/perun_elo.py
from __future__ import annotations

from dataclasses import dataclass
from math import pow
from typing import List, Optional, Tuple

from loguru import logger

# ✅ ЄДИНИЙ правильний pool у твоєму проєкті
try:
    from db import get_pool  # type: ignore
except Exception:
    get_pool = None  # type: ignore


# ────────────────────────────────────────────────────────────────────
# Параметри системи рейтингів Перуна (ELO)
# ────────────────────────────────────────────────────────────────────

K_FACTORS = {
    "day": 48,
    "week": 32,
    "month": 24,
    "all": 16,
}

ELO_FLOOR = 600
ELO_START = 1000


# ────────────────────────────────────────────────────────────────────
# Схема
# ────────────────────────────────────────────────────────────────────

async def ensure_schema() -> bool:
    """
    Таблиця perun_elo з чотирма шкалами (day/week/month/all) + W/L.
    """
    if not get_pool:
        logger.warning("perun_elo.ensure_schema: no DB pool")
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS perun_elo(
                    tg_id BIGINT PRIMARY KEY,
                    elo_day   INT NOT NULL DEFAULT 1000,
                    elo_week  INT NOT NULL DEFAULT 1000,
                    elo_month INT NOT NULL DEFAULT 1000,
                    elo_all   INT NOT NULL DEFAULT 1000,
                    wins      INT NOT NULL DEFAULT 0,
                    losses    INT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT now()
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS perun_elo_updated_idx ON perun_elo(updated_at);")
        return True
    except Exception as e:
        logger.warning(f"perun_elo.ensure_schema failed: {e}")
        return False


async def _ensure_row(conn, tg_id: int) -> None:
    await conn.execute(
        """
        INSERT INTO perun_elo(tg_id)
        VALUES ($1)
        ON CONFLICT (tg_id) DO NOTHING
        """,
        int(tg_id),
    )


# ────────────────────────────────────────────────────────────────────
# Математика ELO
# ────────────────────────────────────────────────────────────────────

def _expected_score(r_a: int, r_b: int) -> float:
    return 1.0 / (1.0 + pow(10.0, (r_b - r_a) / 400.0))


def _apply_elo(r: int, exp: float, score: float, k: int) -> int:
    nr = int(round(r + k * (score - exp)))
    return max(ELO_FLOOR, nr)


# ────────────────────────────────────────────────────────────────────
# Модель рядка
# ────────────────────────────────────────────────────────────────────

@dataclass
class EloRow:
    tg_id: int
    elo_day: int
    elo_week: int
    elo_month: int
    elo_all: int
    wins: int
    losses: int


async def _fetch_row(conn, tg_id: int) -> EloRow:
    row = await conn.fetchrow(
        """
        SELECT tg_id, elo_day, elo_week, elo_month, elo_all, wins, losses
        FROM perun_elo
        WHERE tg_id=$1
        """,
        int(tg_id),
    )
    if not row:
        return EloRow(int(tg_id), ELO_START, ELO_START, ELO_START, ELO_START, 0, 0)
    return EloRow(
        tg_id=int(row["tg_id"]),
        elo_day=int(row["elo_day"]),
        elo_week=int(row["elo_week"]),
        elo_month=int(row["elo_month"]),
        elo_all=int(row["elo_all"]),
        wins=int(row["wins"]),
        losses=int(row["losses"]),
    )


# ────────────────────────────────────────────────────────────────────
# Публічний API
# ────────────────────────────────────────────────────────────────────

async def record_duel_result(winner_id: int, loser_id: int) -> None:
    """
    Зафіксувати результат дуелі: переможець/переможений.
    Оновлює всі 4 шкали ELO (day/week/month/all) і лічильники W/L.
    """
    if not get_pool:
        return
    if not await ensure_schema():
        return

    winner_id = int(winner_id)
    loser_id = int(loser_id)
    if winner_id <= 0 or loser_id <= 0 or winner_id == loser_id:
        return

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _ensure_row(conn, winner_id)
                await _ensure_row(conn, loser_id)

                w = await _fetch_row(conn, winner_id)
                l = await _fetch_row(conn, loser_id)

                # DAY
                e_w = _expected_score(w.elo_day, l.elo_day)
                e_l = 1.0 - e_w
                nw_d = _apply_elo(w.elo_day, e_w, 1.0, K_FACTORS["day"])
                nl_d = _apply_elo(l.elo_day, e_l, 0.0, K_FACTORS["day"])

                # WEEK
                e_w = _expected_score(w.elo_week, l.elo_week)
                e_l = 1.0 - e_w
                nw_w = _apply_elo(w.elo_week, e_w, 1.0, K_FACTORS["week"])
                nl_w = _apply_elo(l.elo_week, e_l, 0.0, K_FACTORS["week"])

                # MONTH
                e_w = _expected_score(w.elo_month, l.elo_month)
                e_l = 1.0 - e_w
                nw_m = _apply_elo(w.elo_month, e_w, 1.0, K_FACTORS["month"])
                nl_m = _apply_elo(l.elo_month, e_l, 0.0, K_FACTORS["month"])

                # ALL
                e_w = _expected_score(w.elo_all, l.elo_all)
                e_l = 1.0 - e_w
                nw_a = _apply_elo(w.elo_all, e_w, 1.0, K_FACTORS["all"])
                nl_a = _apply_elo(l.elo_all, e_l, 0.0, K_FACTORS["all"])

                await conn.execute(
                    """
                    UPDATE perun_elo
                    SET elo_day=$2, elo_week=$3, elo_month=$4, elo_all=$5,
                        wins=wins+1, updated_at=now()
                    WHERE tg_id=$1
                    """,
                    winner_id, nw_d, nw_w, nw_m, nw_a,
                )

                await conn.execute(
                    """
                    UPDATE perun_elo
                    SET elo_day=$2, elo_week=$3, elo_month=$4, elo_all=$5,
                        losses=losses+1, updated_at=now()
                    WHERE tg_id=$1
                    """,
                    loser_id, nl_d, nl_w, nl_m, nl_a,
                )
    except Exception as e:
        logger.warning(f"perun_elo.record_duel_result failed: {e}")


async def reset_period(period: str) -> None:
    """
    Скинути вибрану шкалу (day/week/month) всім гравцям до стартового значення.
    """
    if period not in ("day", "week", "month"):
        return
    if not get_pool:
        return
    try:
        if not await ensure_schema():
            return
        col = f"elo_{period}"
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(f"UPDATE perun_elo SET {col}=$1, updated_at=now()", ELO_START)
    except Exception as e:
        logger.warning(f"perun_elo.reset_period({period}) failed: {e}")


async def get_player_elo(tg_id: int) -> Optional[EloRow]:
    """
    Прочитати поточні рейтинги гравця. Якщо рядка немає — повертає стартові значення.
    """
    if not get_pool:
        return None
    if not await ensure_schema():
        return None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _ensure_row(conn, int(tg_id))
            return await _fetch_row(conn, int(tg_id))
    except Exception as e:
        logger.warning(f"perun_elo.get_player_elo failed: {e}")
        return None


async def top(period: str, limit: int = 20) -> List[Tuple[int, int, int, int, int, int]]:
    """
    Топ за періодом ('day'/'week'/'month'/'all').
    Повертає: (tg_id, elo_day, elo_week, elo_month, elo_all, wins)
    """
    if period not in ("day", "week", "month", "all"):
        period = "all"
    if not get_pool:
        return []
    if not await ensure_schema():
        return []

    order_col = f"elo_{period}"
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT tg_id, elo_day, elo_week, elo_month, elo_all, wins
                FROM perun_elo
                ORDER BY {order_col} DESC, wins DESC, tg_id ASC
                LIMIT $1
                """,
                int(limit),
            )
            return [
                (
                    int(r["tg_id"]),
                    int(r["elo_day"]),
                    int(r["elo_week"]),
                    int(r["elo_month"]),
                    int(r["elo_all"]),
                    int(r["wins"]),
                )
                for r in rows
            ]
    except Exception as e:
        logger.warning(f"perun_elo.top failed: {e}")
        return []


__all__ = [
    "ELO_START",
    "ELO_FLOOR",
    "ensure_schema",
    "record_duel_result",
    "reset_period",
    "get_player_elo",
    "top",
    "EloRow",
]