# services/daily_login.py
from __future__ import annotations

from datetime import date
import random
from loguru import logger

try:
    from db import get_pool  # type: ignore
except Exception:
    get_pool = None  # fallback


REWARDS: dict[int, tuple[int, int]] = {
    1: (10, 50),
    2: (15, 60),
    3: (20, 70),
    4: (25, 80),
    5: (30, 100),
    6: (40, 120),
    7: (75, 200),
}


async def _ensure_schema_and_wallet() -> tuple[bool, str | None]:
    """
    Гарантує наявність полів для daily login та повертає колонку гаманця:
    'chervontsi' або 'coins'.
    """
    if not get_pool:
        return (False, None)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS last_login DATE")
        await conn.execute(
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS login_streak INTEGER NOT NULL DEFAULT 0"
        )
        await conn.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS xp BIGINT NOT NULL DEFAULT 0")
        await conn.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS kleynody BIGINT NOT NULL DEFAULT 0")

        rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name='players'
              AND column_name IN ('chervontsi','coins')
            """
        )
        have = {r["column_name"] for r in rows}
        wallet_col: str | None

        if "chervontsi" in have:
            wallet_col = "chervontsi"
        elif "coins" in have:
            wallet_col = "coins"
        else:
            await conn.execute(
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS chervontsi BIGINT NOT NULL DEFAULT 0"
            )
            wallet_col = "chervontsi"

        await conn.execute(f"UPDATE players SET {wallet_col}=COALESCE({wallet_col},0)")
        await conn.execute("UPDATE players SET xp=COALESCE(xp,0)")
        await conn.execute("UPDATE players SET kleynody=COALESCE(kleynody,0)")

    return (True, wallet_col)


def _as_date(v) -> date | None:
    try:
        if v is None:
            return None
        if isinstance(v, date):
            return v
        return getattr(v, "date")()
    except Exception:
        return None


async def process_daily_login(tg_id: int) -> tuple[int, int, bool]:
    """
    Повертає: (xp_gain, coins_gain, got_kleynod)
    Якщо сьогодні вже отримував — (0, 0, False)
    """
    if not get_pool:
        logger.warning("process_daily_login: no DB pool")
        return (0, 0, False)

    ok, wallet_col = await _ensure_schema_and_wallet()
    if not ok or not wallet_col:
        return (0, 0, False)

    pool = await get_pool()
    today = date.today()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_login, login_streak FROM players WHERE tg_id=$1",
            tg_id,
        )
        if not row:
            return (0, 0, False)

        last_login = _as_date(row["last_login"])
        streak = int(row["login_streak"] or 0)

        if last_login == today:
            return (0, 0, False)

        if last_login is None or (today - last_login).days > 1:
            streak = 1
        else:
            streak += 1

        day = (streak - 1) % 7 + 1
        xp_gain, coins_gain = REWARDS.get(day, (5, 20))

        got_kleynod = (random.randint(1, 100) == 1)
        kleynod_add = 1 if got_kleynod else 0

        await conn.execute(
            f"""
            UPDATE players
               SET last_login   = $1,
                   login_streak = $2,
                   xp           = COALESCE(xp,0) + $3,
                   {wallet_col} = COALESCE({wallet_col},0) + $4,
                   kleynody     = COALESCE(kleynody,0) + $5
             WHERE tg_id = $6
            """,
            today, streak, xp_gain, coins_gain, kleynod_add, tg_id,
        )

    return (xp_gain, coins_gain, got_kleynod)