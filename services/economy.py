# services/economy.py
from __future__ import annotations

from typing import Optional, Tuple
from loguru import logger

# ---------- DB ----------
# у мініапі пул лежить у корені як db.get_pool
try:
    from db import get_pool  # type: ignore
except Exception:
    get_pool = None  # fallback без БД

# ---------- Контент (моби) ----------
# контент може бути відсутнім — тоді даємо мʼякий fallback
try:
    from content.mobs import MOBS  # type: ignore
except Exception:
    MOBS = []  # type: ignore

# ---------- Рефералки ----------
REF_BONUS_INVITER = 20
REF_BONUS_REFERRAL = 20

# Кешована назва «гаманця» у players
_COIN_COL: Optional[str] = None


# =========================
#   СХЕМА ГАМАНЦЯ
# =========================
async def _resolve_coin_col() -> Optional[str]:
    """
    Визначаємо й готуємо колонку гаманця у players:
      - якщо є chervontsi → використовуємо її;
      - інакше якщо є coins → використовуємо її;
      - інакше додаємо chervontsi BIGINT NOT NULL DEFAULT 0.
    """
    global _COIN_COL
    if _COIN_COL is not None:
        return _COIN_COL

    if not get_pool:
        logger.warning("economy: no DB pool; wallet ops disabled")
        return None

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='players'
                  AND column_name IN ('chervontsi','coins')
                """
            )
            have = {r["column_name"] for r in rows}

            if "chervontsi" in have:
                _COIN_COL = "chervontsi"
            elif "coins" in have:
                _COIN_COL = "coins"
            else:
                await conn.execute(
                    "ALTER TABLE players ADD COLUMN IF NOT EXISTS chervontsi BIGINT NOT NULL DEFAULT 0;"
                )
                _COIN_COL = "chervontsi"

            # На всяк випадок приберемо NULL-и
            await conn.execute(f"UPDATE players SET {_COIN_COL}=COALESCE({_COIN_COL},0);")

        logger.info(f"economy: using wallet column '{_COIN_COL}'")
        return _COIN_COL

    except Exception as e:
        logger.warning(f"economy: _resolve_coin_col failed: {e}")
        return None


async def ensure_wallet_schema() -> bool:
    ok = (await _resolve_coin_col()) is not None
    if ok:
        await _ensure_ref_schema()
    return ok


# =========================
#   РЕЄСТРАЦІЯ І РЕФЕРАЛКИ
# =========================
async def _player_exists(conn, tg_id: int) -> bool:
    row = await conn.fetchrow("SELECT 1 FROM players WHERE tg_id=$1", tg_id)
    return bool(row)


async def _ensure_ref_schema() -> None:
    if not get_pool:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS referrals (
                    tg_id         BIGINT PRIMARY KEY,
                    referrer_tg   BIGINT NOT NULL,
                    registered_at TIMESTAMP NOT NULL DEFAULT now(),
                    reward_paid   BOOLEAN NOT NULL DEFAULT FALSE
                );
                """
            )
    except Exception as e:
        logger.warning(f"economy._ensure_ref_schema failed: {e}")


async def _attempt_pay_referral_bonus(tg_id: int) -> bool:
    """
    Виплачує бонус ОДИН раз, але лише якщо:
      - є запис у referrals(tg_id, referrer_tg, reward_paid=FALSE)
      - обидва гравці (tg_id і referrer_tg) вже існують у players.
    """
    col = await _resolve_coin_col()
    if not col or not get_pool:
        return False

    await _ensure_ref_schema()

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                ref = await conn.fetchrow(
                    "SELECT referrer_tg, reward_paid FROM referrals WHERE tg_id=$1 FOR UPDATE",
                    tg_id,
                )
                if not ref or bool(ref["reward_paid"]):
                    return False

                referrer = int(ref["referrer_tg"])

                # Обидва гравці мають вже існувати
                if not (await _player_exists(conn, tg_id)) or not (await _player_exists(conn, referrer)):
                    return False

                # Нарахування
                await conn.execute(
                    f"UPDATE players SET {col}=COALESCE({col},0)+$2 WHERE tg_id=$1",
                    referrer, REF_BONUS_INVITER,
                )
                await conn.execute(
                    f"UPDATE players SET {col}=COALESCE({col},0)+$2 WHERE tg_id=$1",
                    tg_id, REF_BONUS_REFERRAL,
                )

                await conn.execute("UPDATE referrals SET reward_paid=TRUE WHERE tg_id=$1", tg_id)

        logger.info(
            f"referral bonus paid: referrer={referrer} +{REF_BONUS_INVITER}, "
            f"referral={tg_id} +{REF_BONUS_REFERRAL}"
        )
        return True

    except Exception as e:
        logger.warning(f"_attempt_pay_referral_bonus failed for {tg_id}: {e}")
        return False


async def process_pending_referral_rewards(limit: int = 100) -> int:
    col = await _resolve_coin_col()
    if not col or not get_pool:
        return 0

    await _ensure_ref_schema()
    processed = 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tg_id FROM referrals WHERE reward_paid=FALSE LIMIT $1",
                limit,
            )
        for r in rows:
            tg = int(r["tg_id"])
            if await _attempt_pay_referral_bonus(tg):
                processed += 1
        return processed
    except Exception as e:
        logger.warning(f"process_pending_referral_rewards failed: {e}")
        return processed


# =========================
#   ГРОШІ: БАЛАНС/ДОДАТИ/СПИСАТИ
# =========================
async def get_balance(tg_id: int) -> int:
    col = await _resolve_coin_col()
    if not col or not get_pool:
        return 0

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT COALESCE({col},0) AS balance FROM players WHERE tg_id=$1",
                tg_id,
            )
            return int(row["balance"]) if row else 0
    except Exception as e:
        logger.warning(f"economy.get_balance failed: {e}")
        return 0


async def add_coins(tg_id: int, amount: int) -> int:
    col = await _resolve_coin_col()
    if not col or not get_pool:
        return 0

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # тільки для існуючих гравців
            if not await _player_exists(conn, tg_id):
                return 0
            await conn.execute(
                f"""
                UPDATE players
                SET {col} = GREATEST(COALESCE({col},0) + $2, 0)
                WHERE tg_id = $1
                """,
                tg_id, amount,
            )
            row = await conn.fetchrow(
                f"SELECT COALESCE({col},0) AS balance FROM players WHERE tg_id=$1",
                tg_id,
            )
            return int(row["balance"]) if row else 0
    except Exception as e:
        logger.warning(f"economy.add_coins failed: {e}")
        return 0


async def spend_coins(tg_id: int, amount: int) -> bool:
    if amount <= 0 or not get_pool:
        return True

    col = await _resolve_coin_col()
    if not col:
        return False

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if not await _player_exists(conn, tg_id):
                return False
            row = await conn.fetchrow(
                f"SELECT COALESCE({col},0) AS balance FROM players WHERE tg_id=$1",
                tg_id,
            )
            cur = int(row["balance"]) if row else 0
            if cur < amount:
                return False
            await conn.execute(
                f"UPDATE players SET {col}={col}-$2 WHERE tg_id=$1",
                tg_id, amount,
            )
            return True
    except Exception as e:
        logger.warning(f"economy.spend_coins failed: {e}")
        return False


# =========================
#   РОЗРАХУНОК НАГОРОДИ ЗА БІЙ
# =========================
def _get_mob_by_code(mob_code: str):
    # 1) id як рядок
    for m in MOBS:
        mid = getattr(m, "id", None)
        if mid is not None and str(mid) == str(mob_code):
            return m
    # 2) id як int
    try:
        code_int = int(mob_code)
        for m in MOBS:
            mid = getattr(m, "id", None)
            if mid is not None and int(mid) == code_int:
                return m
    except Exception:
        pass
    # 3) по імені
    for m in MOBS:
        nm = getattr(m, "name", None)
        if nm and str(nm) == str(mob_code):
            return m
    return None


def coin_reward_for_mob(mob_code: str, player_level: Optional[int] = None) -> int:
    m = _get_mob_by_code(mob_code)
    if not m:
        return 3
    mob_lvl = int(getattr(m, "level", 1))
    base = 3 + 2 * mob_lvl
    if isinstance(player_level, int) and player_level > 0:
        delta = max(-5, min(6, mob_lvl - player_level))
        base += delta
    return max(2, base)


async def grant_coins_for_win(
    tg_id: int,
    mob_code: str,
    player_level: Optional[int] = None,
) -> Tuple[int, int]:
    """
    Нараховуємо монети за перемогу (тільки якщо гравець вже існує).
    Паралельно намагаємось виплатити реф-бонус (якщо готові умови).
    """
    col = await _resolve_coin_col()
    if not col or not get_pool:
        return (0, 0)

    # Спроба одноразової виплати реф-бонусу
    await _attempt_pay_referral_bonus(tg_id)

    gain = coin_reward_for_mob(mob_code, player_level)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if not await _player_exists(conn, tg_id):
                return (0, 0)
            await conn.execute(
                f"UPDATE players SET {col} = COALESCE({col},0) + $2 WHERE tg_id=$1",
                tg_id, gain,
            )
            row = await conn.fetchrow(
                f"SELECT COALESCE({col},0) AS balance FROM players WHERE tg_id=$1",
                tg_id,
            )
            balance = int(row["balance"]) if row else 0

        logger.info(f"economy.grant_coins_for_win: uid={tg_id} +{gain} → {balance} ({col})")
        return (gain, balance)
    except Exception as e:
        logger.warning(f"grant_coins_for_win failed tg_id={tg_id}: {e}")
        return (0, 0)


# =========================
#   ВІДМІНЮВАННЯ НАЗВИ ВАЛЮТИ
# =========================
def chervonets_name(n: int) -> str:
    n = abs(int(n))
    last_two = n % 100
    last = n % 10
    if 11 <= last_two <= 14:
        return "Червонців"
    if last == 1:
        return "Червонець"
    if 2 <= last <= 4:
        return "Червонці"
    return "Червонців"