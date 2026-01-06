# services/referral_rewards.py
from __future__ import annotations

import os
from typing import Optional, Tuple, Dict, Any
from loguru import logger

# ────────────────────────────────────────────────────────────────────
# DB
# ────────────────────────────────────────────────────────────────────
try:
    from database import get_pool  # type: ignore
except Exception:
    get_pool = None  # pragma: no cover

# ────────────────────────────────────────────────────────────────────
# Економіка (пріоритет — сервіс; якщо його нема, оновлюємо напряму в players)
# ────────────────────────────────────────────────────────────────────
try:
    from services.economy import add_coins as _svc_add_coins  # type: ignore
except Exception:
    _svc_add_coins = None  # type: ignore

try:
    from services.wallet import add_kleynods as _svc_add_k # type: ignore
except Exception:
    _svc_add_k = None  # type: ignore


# ────────────────────────────────────────────────────────────────────
# ENV-настройки (можна міняти без коду)
# ────────────────────────────────────────────────────────────────────
REF_ENABLE = os.getenv("REF_ENABLE", "1") == "1"

INVITEE_COINS = int(os.getenv("REF_REWARD_INVITEE_COINS", "50"))
REFERRER_COINS = int(os.getenv("REF_REWARD_REFERRER_COINS", "50"))
REFERRER_KLEYNODS = int(os.getenv("REF_REWARD_REFERRER_KLEYNODS", "1"))

TXT_INVITEE = os.getenv(
    "REF_MSG_INVITEE",
    "🎉 Дякуємо, що зайшов(ла) за реферальним запрошенням!\n"
    "Отримуєш бонус за перший бій: +{coins} Червонців{plus_k}."
)
TXT_REFERRER = os.getenv(
    "REF_MSG_REFERRER",
    "🤝 Твій реферал виграв перший бій. Нараховано нагороду: +{coins} Червонців{plus_k}."
)


# ────────────────────────────────────────────────────────────────────
# СХЕМА (сумісність із двома варіантами колонок)
#   Варіант А (стандарт):   invitee_id, inviter_id, reward_paid, created_at
#   Варіант B (старіший):   tg_id,     referrer_tg, reward_paid, registered_at
# ────────────────────────────────────────────────────────────────────
_SCHEMA_OK = False

_CREATE_SQL_STD = """
CREATE TABLE IF NOT EXISTS referrals (
    invitee_id  BIGINT PRIMARY KEY,
    inviter_id  BIGINT NOT NULL,
    reward_paid BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS referrals_inviter_idx ON referrals(inviter_id);
"""

_CREATE_SQL_ALT = """
CREATE TABLE IF NOT EXISTS referrals (
    tg_id         BIGINT PRIMARY KEY,
    referrer_tg   BIGINT NOT NULL,
    reward_paid   BOOLEAN  NOT NULL DEFAULT FALSE,
    registered_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_tg);
"""


async def _column_exists(conn, table: str, col: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = $1 AND column_name = $2
        LIMIT 1
        """,
        table, col
    )
    return bool(row)


async def _ensure_schema(conn) -> Tuple[str, str]:
    """
    Гарантує наявність таблиці referrals.
    Повертає кортеж (invitee_col, inviter_col), який треба використовувати в запитах.
    """
    global _SCHEMA_OK
    # Якщо таблиця порожня — створимо стандартну
    for stmt in _CREATE_SQL_STD.strip().split(";"):
        s = stmt.strip()
        if s:
            await conn.execute(s + ";")

    # Якщо ж у БД вже лежить «альтернативна» схема — детектимо її.
    alt_invitee = await _column_exists(conn, "referrals", "tg_id")
    alt_inviter = await _column_exists(conn, "referrals", "referrer_tg")

    if alt_invitee and alt_inviter:
        _SCHEMA_OK = True
        return "tg_id", "referrer_tg"

    # інакше — стандарт
    std_invitee = await _column_exists(conn, "referrals", "invitee_id")
    std_inviter = await _column_exists(conn, "referrals", "inviter_id")
    if std_invitee and std_inviter:
        _SCHEMA_OK = True
        return "invitee_id", "inviter_id"

    # fallback: створимо альтернативну (щоб точно щось було)
    for stmt in _CREATE_SQL_ALT.strip().split(";"):
        s = stmt.strip()
        if s:
            await conn.execute(s + ";")
    _SCHEMA_OK = True
    return "tg_id", "referrer_tg"


async def ensure_schema() -> bool:
    if not get_pool:
        logger.warning("referral_rewards.ensure_schema: no DB pool")
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _ensure_schema(conn)
        return True
    except Exception as e:
        logger.warning(f"referral_rewards.ensure_schema failed: {e}")
        return False


# ────────────────────────────────────────────────────────────────────
# ХЕЛПЕРИ ГАМАНЦЯ (fallback, якщо немає сервісів)
# ────────────────────────────────────────────────────────────────────
async def _ensure_player_exists(conn, tg_id: int) -> None:
    await conn.execute(
        """
        INSERT INTO players (tg_id, name, level)
        VALUES ($1, COALESCE((SELECT name FROM players WHERE tg_id=$1), 'Герой'), 1)
        ON CONFLICT (tg_id) DO NOTHING
        """,
        tg_id,
    )


async def _wallet_col(conn) -> str:
    # Пріоритет chervontsi → coins
    if await _column_exists(conn, "players", "chervontsi"):
        col = "chervontsi"
    elif await _column_exists(conn, "players", "coins"):
        col = "coins"
    else:
        await conn.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS chervontsi INT DEFAULT 0;")
        col = "chervontsi"
    await conn.execute(f"UPDATE players SET {col}=COALESCE({col},0);")
    return col


async def _fallback_add_coins(conn, tg_id: int, delta: int) -> int:
    await _ensure_player_exists(conn, tg_id)
    col = await _wallet_col(conn)
    await conn.execute(f"UPDATE players SET {col}={col}+$2 WHERE tg_id=$1", tg_id, int(delta))
    row = await conn.fetchrow(f"SELECT COALESCE({col},0) AS b FROM players WHERE tg_id=$1", tg_id)
    return int(row["b"] if row else 0)


async def _fallback_add_kleynods(conn, tg_id: int, delta: int) -> int:
    await conn.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS kleynody INT NOT NULL DEFAULT 0;")
    await _ensure_player_exists(conn, tg_id)
    await conn.execute("UPDATE players SET kleynody=COALESCE(kleynody,0)+$2 WHERE tg_id=$1", tg_id, int(delta))
    row = await conn.fetchrow("SELECT COALESCE(kleynody,0) AS k FROM players WHERE tg_id=$1", tg_id)
    return int(row["k"] if row else 0)


# ────────────────────────────────────────────────────────────────────
# ВНУТРІШНІ УТИЛІТИ ДЛЯ REFERRALS
# ────────────────────────────────────────────────────────────────────
async def _select_referrer(conn, invitee_col: str, inviter_col: str, invitee_id: int) -> Optional[int]:
    row = await conn.fetchrow(
        f"SELECT {inviter_col} AS inviter FROM referrals WHERE {invitee_col}=$1",
        invitee_id,
    )
    return int(row["inviter"]) if row else None


async def _is_paid(conn, invitee_col: str, invitee_id: int) -> bool:
    row = await conn.fetchrow(
        f"SELECT reward_paid FROM referrals WHERE {invitee_col}=$1",
        invitee_id,
    )
    return bool(row and row["reward_paid"])


async def _mark_paid(conn, invitee_col: str, invitee_id: int) -> None:
    await conn.execute(
        f"UPDATE referrals SET reward_paid=TRUE WHERE {invitee_col}=$1",
        invitee_id,
    )


# ────────────────────────────────────────────────────────────────────
# ПУБЛІЧНЕ API: прив’язка
# ────────────────────────────────────────────────────────────────────
async def link_referral(invitee_tg: int, referrer_tg: int) -> bool:
    """
    Ідемпотентна прив’язка «кого запросили → хто запросив».
    Не перезаписує існуючий запис.
    """
    if not get_pool:
        return False
    if invitee_tg == referrer_tg:
        return False

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            inv_col, ref_col = await _ensure_schema(conn)

            row = await conn.fetchrow(
                f"SELECT {ref_col} FROM referrals WHERE {inv_col}=$1",
                invitee_tg,
            )
            if row:
                return True

            await conn.execute(
                f"""
                INSERT INTO referrals ({inv_col}, {ref_col})
                VALUES ($1, $2)
                ON CONFLICT ({inv_col}) DO NOTHING
                """,
                invitee_tg, referrer_tg,
            )
            return True


# Сумісна назва з попередньої версії
async def set_referrer(invitee_id: int, inviter_id: int) -> bool:
    return await link_referral(invitee_id, inviter_id)


async def get_referrer(invitee_id: int) -> Optional[Tuple[int, bool]]:
    """
    Повертає (inviter_id, reward_paid) або None.
    """
    if not get_pool:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        inv_col, ref_col = await _ensure_schema(conn)
        row = await conn.fetchrow(
            f"SELECT {ref_col} AS inviter_id, reward_paid FROM referrals WHERE {inv_col}=$1",
            invitee_id,
        )
    if not row:
        return None
    return int(row["inviter_id"]), bool(row["reward_paid"])


# ────────────────────────────────────────────────────────────────────
# ПУБЛІЧНЕ API: виплата після першої перемоги
# ────────────────────────────────────────────────────────────────────
async def reward_after_first_win(invitee_tg: int) -> Dict[str, int]:
    """
    Виплата без повідомлень у бот (чистий бізнес-лог).
    Повертає dict з нарахуваннями.
    """
    if not get_pool or not REF_ENABLE:
        return {"invitee": 0, "inviter": 0, "inviter_kleynody": 0}

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            inv_col, ref_col = await _ensure_schema(conn)

            if await _is_paid(conn, inv_col, invitee_tg):
                return {"invitee": 0, "inviter": 0, "inviter_kleynody": 0}

            inviter_tg = await _select_referrer(conn, inv_col, ref_col, invitee_tg)
            if not inviter_tg:
                return {"invitee": 0, "inviter": 0, "inviter_kleynody": 0}

            # Нарахування
            invitee_gain = 0
            inviter_gain = 0
            inviter_k_gain = 0

            # coins через сервіс або напряму
            if INVITEE_COINS > 0:
                if _svc_add_coins:
                    try:
                        await _svc_add_coins(invitee_tg, INVITEE_COINS)
                    except Exception:
                        await _fallback_add_coins(conn, invitee_tg, INVITEE_COINS)
                else:
                    await _fallback_add_coins(conn, invitee_tg, INVITEE_COINS)
                invitee_gain = INVITEE_COINS

            if REFERRER_COINS > 0:
                if _svc_add_coins:
                    try:
                        await _svc_add_coins(inviter_tg, REFERRER_COINS)
                    except Exception:
                        await _fallback_add_coins(conn, inviter_tg, REFERRER_COINS)
                else:
                    await _fallback_add_coins(conn, inviter_tg, REFERRER_COINS)
                inviter_gain = REFERRER_COINS

            if REFERRER_KLEYNODS > 0:
                if _svc_add_k:
                    try:
                        await _svc_add_k(inviter_tg, REFERRER_KLEYNODS)
                    except Exception:
                        inviter_k_gain = await _fallback_add_kleynods(conn, inviter_tg, REFERRER_KLEYNODS)
                else:
                    inviter_k_gain = await _fallback_add_kleynods(conn, inviter_tg, REFERRER_KLEYNODS)

            await _mark_paid(conn, inv_col, invitee_tg)

            return {
                "invitee": invitee_gain,
                "inviter": inviter_gain,
                "inviter_kleynody": int(inviter_k_gain),
            }


async def pay_tutorial_rewards(invitee_id: int, *, bot=None) -> bool:
    """
    Обгортка над reward_after_first_win з кастомними повідомленнями у бот (якщо передали).
    """
    result = await reward_after_first_win(invitee_id)
    if not any(result.values()):
        return False

    if bot:
        plus_k = f", +{REFERRER_KLEYNODS} клейнодів" if REFERRER_KLEYNODS else ""
        try:
            if result["invitee"]:
                await bot.send_message(
                    invitee_id,
                    TXT_INVITEE.format(coins=result["invitee"], plus_k="")
                )
        except Exception:
            pass
        try:
            if result["inviter"] or REFERRER_KLEYNODS:
                inviter_tg = None
                # дістати інвайтера, щоб надіслати повідомлення
                ref = await get_referrer(invitee_id)
                if ref:
                    inviter_tg = ref[0]
                if inviter_tg:
                    await bot.send_message(
                        inviter_tg,
                        TXT_REFERRER.format(coins=result["inviter"], plus_k=plus_k)
                    )
        except Exception:
            pass

    return True


__all__ = [
    "ensure_schema",
    "link_referral",
    "set_referrer",
    "get_referrer",
    "reward_after_first_win",
    "pay_tutorial_rewards",
]