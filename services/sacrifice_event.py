# services/sacrifice_event.py
from __future__ import annotations

import datetime
from typing import Optional, List, Dict, Tuple
from loguru import logger

# ----- DB pool -------------------------------------------------
try:
    from database import get_pool  # type: ignore
except Exception:
    get_pool = None  # fallback no-db

# ----- економіка гравця (червонці) -----------------------------
try:
    from services.economy import spend_coins, get_balance as get_coins_balance  # type: ignore
except Exception:
    async def spend_coins(_tg_id: int, _amount: int) -> bool:
        return False
    async def get_coins_balance(_tg_id: int) -> int:
        return 0

# Можливе поповнення монет (для рефанду)
try:
    from services.economy import add_coins  # type: ignore
except Exception:
    async def add_coins(_tg_id: int, _delta: int) -> int:
        # фолбек — повернемо False у safe_refund, якщо нема
        raise RuntimeError("add_coins unavailable")

# ----- преміум валюта (клейноди) -------------------------------
try:
    from services.wallet import add_kleynods  # type: ignore
except Exception:
    async def add_kleynods(_tg_id: int, _delta: int) -> int:
        return 0

# ----- прогрес застави (рівень/XP) -----------------------------
try:
    from services.fort_levels import (
        add_fort_xp,
        get_fort_level,
        ensure_schema as ensure_fort_levels_schema,
    )  # type: ignore
except Exception:
    async def ensure_fort_levels_schema() -> bool:
        return False
    async def add_fort_xp(_fort_id: int, _gain: int) -> Tuple[int, int, int, int]:
        # applied_gain, new_level, total_xp_in_level, need_after
        return (0, 1, 0, 0)
    async def get_fort_level(_fid: int) -> Tuple[int, int, int]:
        # (level, xp, need)
        return (1, 0, 50)


# ==============================================================
# КЛЮЧІ ЧАСУ (поточний місяць)
# ==============================================================

def _current_year_month(now: Optional[datetime.datetime] = None) -> Tuple[int, int]:
    if now is None:
        now = datetime.datetime.utcnow()
    return now.year, now.month


# ==============================================================
# СХЕМА
# ==============================================================

_SCHEMA_OK = False

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fort_sacrifice_competition (
    fort_id BIGINT NOT NULL REFERENCES forts(id) ON DELETE CASCADE,
    year INT NOT NULL,
    month INT NOT NULL,
    donated_sum BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (fort_id, year, month)
);

CREATE TABLE IF NOT EXISTS fort_sacrifice_winners (
    year INT NOT NULL,
    month INT NOT NULL,
    place INT NOT NULL,
    fort_id BIGINT NOT NULL,
    reward_xp INT NOT NULL DEFAULT 0,
    reward_kleynods INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

-- унікальність запису по періоду і місцю
CREATE UNIQUE INDEX IF NOT EXISTS fort_sacrifice_winners_uniq
    ON fort_sacrifice_winners(year, month, place);

CREATE INDEX IF NOT EXISTS fort_sacrifice_competition_ym
    ON fort_sacrifice_competition(year, month);

CREATE INDEX IF NOT EXISTS fort_sacrifice_winners_ym
    ON fort_sacrifice_winners(year, month);
"""


async def ensure_schema() -> bool:
    global _SCHEMA_OK
    if _SCHEMA_OK:
        return True
    if not get_pool:
        logger.warning("sacrifice_event.ensure_schema: no DB pool")
        return False

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # розіб’ємо на стейтменти
            for stmt in SCHEMA_SQL.split(";"):
                sql = stmt.strip()
                if sql:
                    await conn.execute(sql + ";")

        try:
            await ensure_fort_levels_schema()
        except Exception as e:
            logger.warning(f"sacrifice_event.ensure_fort_levels_schema warn: {e}")

        _SCHEMA_OK = True
        return True
    except Exception as e:
        logger.warning(f"sacrifice_event.ensure_schema failed: {e}")
        return False


# ==============================================================
# ВНУТРІШНІ ХЕЛПЕРИ
# ==============================================================

async def _is_member_of_fort(tg_id: int, fort_id: int) -> bool:
    """
    Перевіряємо, що гравець є учасником саме цієї застави.
    """
    if not get_pool:
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM fort_members WHERE tg_id=$1 AND fort_id=$2 LIMIT 1",
                tg_id, fort_id,
            )
            return bool(row)
    except Exception as e:
        logger.warning(f"sacrifice_event._is_member_of_fort failed: {e}")
        return False


async def _safe_refund(tg_id: int, amount: int) -> None:
    """
    Повертаємо монети гравцю після збою.
    Спочатку пробуємо add_coins, якщо нема — як фолбек пробуємо spend_coins з від’ємним значенням.
    """
    if amount <= 0:
        return
    try:
        try:
            await add_coins(tg_id, amount)
        except Exception:
            # fallback, якщо твоє spend_coins дозволяє від’ємні значення
            ok = await spend_coins(tg_id, -amount)
            if not ok:
                logger.error(f"sacrifice_event._safe_refund failed for uid={tg_id}, amount={amount}")
    except Exception as e:
        logger.error(f"sacrifice_event._safe_refund exception: {e}")


# ==============================================================
# ГОЛОВНА ДІЯ ГРАВЦЯ: ПРИНЕСТИ ЖЕРТВУ
# ==============================================================

async def record_sacrifice(tg_id: int, fort_id: int, amount: int) -> Tuple[bool, str]:
    """
    Гравець намагається пожертвувати amount Червонців на вівтар своєї застави.

    Кроки:
      - ensure_schema()
      - перевірити членство у форті
      - транзакційно списати монети + оновити таблицю змагання
    """
    if amount <= 0:
        return (False, "Сума повинна бути більшою за нуль.")
    if not await ensure_schema():
        return (False, "Сервіс недоступний. Спробуй пізніше.")

    # 0) захист від лайфхаку чужим fort_id
    if not await _is_member_of_fort(tg_id, fort_id):
        return (False, "Ти не є учасником цієї застави.")

    y, m = _current_year_month()

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # 1) списання монет
                ok = await spend_coins(tg_id, amount)
                if not ok:
                    bal = await get_coins_balance(tg_id)
                    return (False, f"Недостатньо Червонців. Маєш {bal}, потрібно {amount}.")

                # 2) оновлення турнірної таблиці
                await conn.execute(
                    """
                    INSERT INTO fort_sacrifice_competition(fort_id, year, month, donated_sum)
                    VALUES ($1,$2,$3,$4)
                    ON CONFLICT (fort_id, year, month)
                    DO UPDATE SET
                        donated_sum = fort_sacrifice_competition.donated_sum + EXCLUDED.donated_sum,
                        updated_at = now()
                    """,
                    fort_id, y, m, amount,
                )

    except Exception as e:
        logger.warning(f"sacrifice_event.record_sacrifice failed fort={fort_id} uid={tg_id}: {e}")
        # бест-ефорт рефанд
        await _safe_refund(tg_id, amount)
        return (False, "Щось пішло не так під час жертви. Гроші повернено.")

    return (True, f"🕯 Твоя жертва {amount} Червонців прийнята богами.")


# ==============================================================
# ЛІДЕРБОРД ЗА ПОТОЧНИЙ МІСЯЦЬ
# ==============================================================

async def get_month_leaderboard(limit: int = 10) -> List[Dict]:
    """
    [
      {"place":1,"fort_id":12,"fort_name":"Застава Вогню","sum":12345},
      ...
    ]
    """
    if not await ensure_schema():
        return []

    y, m = _current_year_month()
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT f.id,
                       f.name,
                       s.donated_sum
                FROM fort_sacrifice_competition AS s
                JOIN forts AS f ON f.id = s.fort_id
                WHERE s.year=$1 AND s.month=$2
                ORDER BY s.donated_sum DESC, f.id ASC
                LIMIT $3;
                """,
                y, m, limit,
            )
        out: List[Dict] = []
        for i, r in enumerate(rows, start=1):
            out.append(
                {
                    "place": i,
                    "fort_id": int(r["id"]),
                    "fort_name": r["name"] or f"#{r['id']}",
                    "sum": int(r["donated_sum"]),
                }
            )
        return out
    except Exception as e:
        logger.warning(f"sacrifice_event.get_month_leaderboard failed: {e}")
        return []


async def get_fort_rank_this_month(fort_id: int) -> Optional[Dict]:
    """
    { "place":4, "fort_id":12, "fort_name":"...", "sum":8123 } | None
    """
    if not await ensure_schema():
        return None

    y, m = _current_year_month()
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                WITH ranked AS (
                    SELECT
                      s.fort_id,
                      s.donated_sum,
                      RANK() OVER (ORDER BY s.donated_sum DESC, s.fort_id ASC) AS place
                    FROM fort_sacrifice_competition AS s
                    WHERE s.year=$1 AND s.month=$2
                )
                SELECT r.place, r.fort_id, r.donated_sum, f.name
                FROM ranked r
                JOIN forts f ON f.id = r.fort_id
                WHERE r.fort_id=$3;
                """,
                y, m, fort_id,
            )
        if not row:
            return None
        return {
            "place": int(row["place"]),
            "fort_id": int(row["fort_id"]),
            "fort_name": row["name"] or f"#{row['fort_id']}",
            "sum": int(row["donated_sum"]),
        }
    except Exception as e:
        logger.warning(f"sacrifice_event.get_fort_rank_this_month failed: {e}")
        return None


# ==============================================================
# ФІНАЛІЗАЦІЯ МІСЯЦЯ
# ==============================================================

async def finalize_month(bot=None) -> List[Dict]:
    """
    Кінець місяця:
      1) беремо попередній місяць
      2) витягаємо топ-3
      3) XP форту + клейноди лідеру
      4) лог у winners
      5) (опц) DM лідеру
    """
    if not await ensure_schema():
        return []

    now = datetime.datetime.utcnow()
    first_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_last_day = first_this_month - datetime.timedelta(seconds=1)
    y = prev_month_last_day.year
    m = prev_month_last_day.month

    rewards = {
        1: {"xp": 1000, "kleynods": 5},
        2: {"xp": 500,  "kleynods": 2},
        3: {"xp": 200,  "kleynods": 1},
    }

    winners: List[Dict] = []

    try:
        pool = await get_pool()

        # топ-3
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT f.id,
                       f.name,
                       s.donated_sum
                FROM fort_sacrifice_competition AS s
                JOIN forts AS f ON f.id = s.fort_id
                WHERE s.year=$1 AND s.month=$2
                ORDER BY s.donated_sum DESC, f.id ASC
                LIMIT 3;
                """,
                y, m,
            )

        async def _get_leader_uid(fid: int) -> Optional[int]:
            async with pool.acquire() as conn2:
                row = await conn2.fetchrow(
                    """
                    SELECT tg_id
                    FROM fort_members
                    WHERE fort_id=$1 AND role IN ('hetman','head')
                    ORDER BY CASE role
                        WHEN 'hetman' THEN 1
                        WHEN 'head'   THEN 2
                        ELSE 99
                    END,
                    COALESCE(joined_at, now()) ASC
                    LIMIT 1
                    """,
                    fid,
                )
                if row:
                    return int(row["tg_id"])
                row2 = await conn2.fetchrow(
                    """
                    SELECT tg_id
                    FROM fort_members
                    WHERE fort_id=$1
                    ORDER BY COALESCE(joined_at, now()) ASC
                    LIMIT 1
                    """,
                    fid,
                )
                if row2:
                    return int(row2["tg_id"])
                return None

        place_counter = 1
        for r in rows:
            if place_counter > 3:
                break
            fid = int(r["id"])
            fname = r["name"] or f"#{fid}"
            sum_donated = int(r["donated_sum"])

            rw = rewards.get(place_counter, {"xp": 0, "kleynods": 0})
            xp_gain = int(rw["xp"])
            k_gain = int(rw["kleynods"])

            if xp_gain > 0:
                try:
                    await add_fort_xp(fid, xp_gain)
                except Exception as e:
                    logger.warning(f"sacrifice_event.finalize_month add_fort_xp fail fort={fid}: {e}")

            leader_uid = await _get_leader_uid(fid)
            if leader_uid and k_gain > 0:
                try:
                    await add_kleynods(leader_uid, k_gain)
                except Exception as e:
                    logger.warning(f"sacrifice_event.finalize_month add_kleynods fail uid={leader_uid}: {e}")

            try:
                async with pool.acquire() as conn3:
                    await conn3.execute(
                        """
                        INSERT INTO fort_sacrifice_winners
                        (year, month, place, fort_id, reward_xp, reward_kleynods, created_at)
                        VALUES ($1,$2,$3,$4,$5,$6,now())
                        ON CONFLICT (year, month, place) DO NOTHING;
                        """,
                        y, m, place_counter, fid, xp_gain, k_gain,
                    )
            except Exception as e:
                logger.warning(f"sacrifice_event.finalize_month insert winner failed: {e}")

            winners.append(
                {
                    "place": place_counter,
                    "fort_id": fid,
                    "fort_name": fname,
                    "sum": sum_donated,
                    "reward_xp": xp_gain,
                    "reward_kleynods": k_gain,
                }
            )

            if bot and leader_uid:
                try:
                    lines = [
                        f"🕯 Жертва Богам завершена.",
                        f"Твоя застава «{fname}» взяла {place_counter}-е місце!",
                        f"Принесено: {sum_donated} Червонців.",
                    ]
                    if xp_gain:
                        lines.append(f"+{xp_gain} досвіду заставі.")
                    if k_gain:
                        lines.append(f"+{k_gain} клейнодів особисто тобі.")
                    await bot.send_message(leader_uid, "\n".join(lines))
                except Exception:
                    pass

            place_counter += 1

        return winners

    except Exception as e:
        logger.warning(f"sacrifice_event.finalize_month failed: {e}")
        return []