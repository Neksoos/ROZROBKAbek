# services/wallet.py
from __future__ import annotations

from loguru import logger

try:
    from database import get_pool  # async getter for asyncpg pool
except Exception:
    get_pool = None


# ---------------- Schema ----------------

async def ensure_wallet_schema() -> bool:
    """
    Працюємо тільки з players.kleynody.
    Гарантуємо наявність колонки, прибираємо NULL і фіксуємо NOT NULL + DEFAULT 0.
    """
    if not get_pool:
        logger.warning("wallet: no DB pool; kleynody will be NO-OP")
        return False

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # додати колонку, якщо її нема
            await conn.execute("""
                ALTER TABLE players
                ADD COLUMN IF NOT EXISTS kleynody INT;
            """)
            # привести існуючі значення
            await conn.execute("""
                UPDATE players
                SET kleynody = COALESCE(kleynody, 0)
                WHERE kleynody IS NULL;
            """)
            # зафіксувати NOT NULL + DEFAULT 0
            await conn.execute("""
                ALTER TABLE players
                ALTER COLUMN kleynody SET DEFAULT 0,
                ALTER COLUMN kleynody SET NOT NULL;
            """)
        return True
    except Exception as e:
        logger.warning(f"wallet.ensure_wallet_schema failed: {e}")
        return False


# ---------------- Helpers ----------------

async def _is_registered_player(conn, tg_id: int) -> bool:
    """
    Вважаємо зареєстрованим лише того, хто має рядок у players та непорожнє name.
    """
    row = await conn.fetchrow(
        "SELECT name FROM players WHERE tg_id=$1",
        tg_id,
    )
    return bool(row and row["name"])


async def _ensure_registered_or_raise(tg_id: int) -> None:
    """
    Використовуй перед будь-яким записом. Не створює нічого у players!
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        ok = await _is_registered_player(conn, tg_id)
        if not ok:
            raise ValueError("player_not_registered")


# ---------------- Public API ----------------

async def get_kleynods(tg_id: int) -> int:
    """
    Повертає баланс клейнодів (0, якщо гравця нема або схема недоступна).
    """
    ok = await ensure_wallet_schema()
    if not ok or not get_pool:
        return 0

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT kleynody AS bal FROM players WHERE tg_id=$1",
                tg_id,
            )
            return int(row["bal"]) if row and row["bal"] is not None else 0
    except Exception as e:
        logger.warning(f"wallet.get_kleynods failed tg_id={tg_id}: {e}")
        return 0


async def set_kleynods(tg_id: int, amount: int) -> int:
    """
    Жорстко встановити баланс (не нижче 0). Для зареєстрованих гравців.
    """
    amount = max(0, int(amount))
    ok = await ensure_wallet_schema()
    if not ok or not get_pool:
        return 0

    await _ensure_registered_or_raise(tg_id)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE players SET kleynody=$2 WHERE tg_id=$1",
                tg_id, amount
            )
            row = await conn.fetchrow(
                "SELECT kleynody AS bal FROM players WHERE tg_id=$1",
                tg_id
            )
            return int(row["bal"]) if row else 0
    except Exception as e:
        logger.warning(f"wallet.set_kleynods failed tg_id={tg_id}: {e}")
        return 0


async def add_kleynods(tg_id: int, delta: int) -> int:
    """
    Додати/відняти клейноди (не дає піти в мінус). Для зареєстрованих гравців.
    """
    delta = int(delta)
    if delta == 0:
        return await get_kleynods(tg_id)

    ok = await ensure_wallet_schema()
    if not ok or not get_pool:
        return 0

    await _ensure_registered_or_raise(tg_id)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE players
                SET kleynody = GREATEST(0, kleynody + $2)
                WHERE tg_id=$1
                """,
                tg_id, delta
            )
            row = await conn.fetchrow(
                "SELECT kleynody AS bal FROM players WHERE tg_id=$1",
                tg_id
            )
            return int(row["bal"]) if row else 0
    except Exception as e:
        logger.warning(f"wallet.add_kleynods failed tg_id={tg_id}, delta={delta}: {e}")
        return 0


async def spend_kleynods(tg_id: int, cost: int) -> bool:
    """
    Атомарно списати `cost` (якщо вистачає). Для зареєстрованих гравців.
    """
    cost = int(cost)
    if cost <= 0:
        return True

    ok = await ensure_wallet_schema()
    if not ok or not get_pool:
        return False

    await _ensure_registered_or_raise(tg_id)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT kleynody FROM players WHERE tg_id=$1 FOR UPDATE",
                    tg_id
                )
                cur = int(row["kleynody"]) if row else 0
                if cur < cost:
                    return False
                await conn.execute(
                    "UPDATE players SET kleynody = kleynody - $2 WHERE tg_id=$1",
                    tg_id, cost
                )
        return True
    except Exception as e:
        logger.warning(f"wallet.spend_kleynods failed tg_id={tg_id}, cost={cost}: {e}")
        return False


async def transfer_kleynods(src_tg_id: int, dst_tg_id: int, amount: int) -> bool:
    """
    Переказ із src → dst. Обидва мають бути зареєстровані.
    """
    amount = int(amount)
    if amount <= 0:
        return True

    ok = await ensure_wallet_schema()
    if not ok or not get_pool:
        return False

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # обидва мають існувати і мати name
                if not await _is_registered_player(conn, src_tg_id):
                    raise ValueError("player_not_registered_src")
                if not await _is_registered_player(conn, dst_tg_id):
                    raise ValueError("player_not_registered_dst")

                # заблокувати джерело
                row = await conn.fetchrow(
                    "SELECT kleynody FROM players WHERE tg_id=$1 FOR UPDATE",
                    src_tg_id
                )
                cur = int(row["kleynody"]) if row else 0
                if cur < amount:
                    return False

                await conn.execute(
                    "UPDATE players SET kleynody = kleynody - $2 WHERE tg_id=$1",
                    src_tg_id, amount
                )
                await conn.execute(
                    "UPDATE players SET kleynody = kleynody + $2 WHERE tg_id=$1",
                    dst_tg_id, amount
                )
        return True
    except ValueError as e:
        logger.warning(f"wallet.transfer_kleynods blocked: {e}")
        return False
    except Exception as e:
        logger.warning(
            f"wallet.transfer_kleynods failed {src_tg_id}->{dst_tg_id} amount={amount}: {e}"
        )
        return False