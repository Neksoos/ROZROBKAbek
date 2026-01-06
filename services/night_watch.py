# services/night_watch.py
from __future__ import annotations

import datetime
import random
from typing import Optional, List, Dict, Tuple
from loguru import logger

# ---------------- DB pool ----------------
# проект має db.py у корені
try:
    from db import get_pool  # type: ignore
except Exception:
    get_pool = None  # fallback

# ---------------- зовнішні сервіси для нагород ----------------
# клейноди (преміум)
try:
    from services.wallet import add_kleynody  # актуальна функція гаманця клейнодів
except Exception:
    async def add_kleynody(_tg_id: int, _delta: int) -> int:  # fallback
        return 0

# червонці (звичайні гроші)
try:
    from services.economy import add_coins  # type: ignore
except Exception:
    async def add_coins(_tg_id: int, _amount: int) -> int:  # fallback
        return 0


# ============================================================
# ТИЖДЕНЬ
# ============================================================

def _current_week_key(now: Optional[datetime.datetime] = None) -> Tuple[int, int]:
    """
    Вертає (рік, номер_тижня по ISO).
    Це ключ сезону Нічної Варти.
    """
    if now is None:
        now = datetime.datetime.utcnow()
    y, w, _ = now.isocalendar()
    return int(y), int(w)


# ============================================================
# СХЕМА
# ============================================================

_SCHEMA_OK = False


async def ensure_schema() -> bool:
    """
    Таблиці:
      night_watch_progress:
        tg_id, week_year, week_num -> PRIMARY KEY
        kills_total
        hp_destroyed
        medals
      night_watch_winners:
        записуємо топ-3 щотижня з нагородами
        (поле для преміум-винагород називається reward_kleynody)
    """
    global _SCHEMA_OK
    if _SCHEMA_OK:
        return True
    if not get_pool:
        logger.warning("night_watch.ensure_schema: no DB pool")
        return False

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS night_watch_progress (
                    tg_id BIGINT NOT NULL,
                    week_year INT NOT NULL,
                    week_num INT NOT NULL,
                    kills_total INT NOT NULL DEFAULT 0,
                    hp_destroyed BIGINT NOT NULL DEFAULT 0,
                    medals INT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP NOT NULL DEFAULT now(),
                    PRIMARY KEY (tg_id, week_year, week_num)
                );
            """)

            # ВАЖЛИВО: тут поле називається reward_kleynody
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS night_watch_winners (
                    week_year INT NOT NULL,
                    week_num INT NOT NULL,
                    place INT NOT NULL,
                    tg_id BIGINT NOT NULL,
                    reward_coins INT NOT NULL DEFAULT 0,
                    reward_kleynody INT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT now()
                );
            """)

        _SCHEMA_OK = True
        return True
    except Exception as e:
        logger.warning(f"night_watch.ensure_schema failed: {e}")
        return False


# ============================================================
# ШАНС МЕДАЛІ
# ============================================================

def medal_drop_chance(mob_level: int) -> float:
    """
    Скільки % шанс на 🏅 Медаль Сторожа з моба цього рівня.

    Було дуже мало на низьких рівнях, виглядало як "не падає взагалі".
    Тепер:
      базово ≈ 1.0%
      +0.4% за рівень моба
      максимум 20%
    """
    base, scale = 0.01, 0.004  # 1.0% + 0.4% * level
    c = base + scale * max(1, mob_level)
    return min(max(c, 0.0001), 0.20)


def roll_medal(mob_level: int, rng: Optional[random.Random] = None) -> bool:
    """
    Кидаємо кубик на медаль.
    rng передаємо ззовні, щоб сид був детермінований.
    """
    rng = rng or random.Random()
    return rng.random() < medal_drop_chance(mob_level)


# ============================================================
# ЗАПИС КІЛУ
# ============================================================

async def report_kill(tg_id: int, mob_level: int, mob_hp_max: int, medal_gained: bool) -> None:
    """
    Викликається коли гравець убив моба.
    - створює/оновлює запис на поточний тиждень
    - kills_total += 1
    - hp_destroyed += HP моба (беремо повний base_hp як "скільки знищено")
    - medals += 1 якщо випала медаль
    """
    if not await ensure_schema():
        return
    wy, wn = _current_week_key()
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO night_watch_progress
                (tg_id, week_year, week_num, kills_total, hp_destroyed, medals, updated_at)
                VALUES ($1,$2,$3,1,$4,$5,now())
                ON CONFLICT (tg_id, week_year, week_num)
                DO UPDATE SET
                    kills_total = night_watch_progress.kills_total + 1,
                    hp_destroyed = night_watch_progress.hp_destroyed + EXCLUDED.hp_destroyed,
                    medals = night_watch_progress.medals + EXCLUDED.medals,
                    updated_at = now();
            """, tg_id, wy, wn, int(mob_hp_max), 1 if medal_gained else 0)
    except Exception as e:
        logger.warning(f"night_watch.report_kill failed {tg_id}: {e}")


# ============================================================
# ХУКИ З BOJІВ (battle.py)
# ============================================================

async def on_battle_win(tg_id: int, mob_level: int, mob_hp_max: int) -> bool:
    """
    Викликається з battle.py при перемозі над мобом.
    - рахує шанс медалі
    - записує кіл у night_watch_progress
    Повертає: True, якщо медаль дропнулась.
    """
    try:
        if mob_level <= 0:
            mob_level = 1
        medal = roll_medal(mob_level)
        await report_kill(tg_id, mob_level, mob_hp_max, medal_gained=medal)
        return medal
    except Exception as e:
        logger.warning(f"night_watch.on_battle_win failed {tg_id}: {e}")
        return False


async def on_battle_loss(tg_id: int, mob_level: int, mob_hp_max: int) -> None:
    """
    Викликається з battle.py при поразці героя.
    Поки що тільки для логів/майбутніх розширень.
    """
    try:
        if not await ensure_schema():
            return
        logger.debug(f"night_watch.on_battle_loss tg_id={tg_id} lvl={mob_level} hp={mob_hp_max}")
    except Exception as e:
        logger.warning(f"night_watch.on_battle_loss failed {tg_id}: {e}")


async def on_battle_flee(tg_id: int, mob_level: int, mob_hp_max: int) -> None:
    """
    Викликається з battle.py при втечі героя.
    Теж суто для статистики в майбутньому.
    """
    try:
        if not await ensure_schema():
            return
        logger.debug(f"night_watch.on_battle_flee tg_id={tg_id} lvl={mob_level} hp={mob_hp_max}")
    except Exception as e:
        logger.warning(f"night_watch.on_battle_flee failed {tg_id}: {e}")


# ============================================================
# РЕЙТИНГ З ІМЕНАМИ
# ============================================================

async def get_week_leaderboard(limit: int = 10) -> List[Dict]:
    """Топ поточного тижня."""
    if not await ensure_schema():
        return []

    wy, wn = _current_week_key()
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT p.name,
                       nw.tg_id,
                       nw.medals,
                       nw.hp_destroyed,
                       nw.kills_total
                FROM night_watch_progress AS nw
                LEFT JOIN players AS p ON p.tg_id = nw.tg_id
                WHERE nw.week_year=$1 AND nw.week_num=$2
                ORDER BY nw.medals DESC,
                         nw.hp_destroyed DESC,
                         nw.kills_total DESC,
                         nw.tg_id ASC
                LIMIT $3;
            """, wy, wn, limit)

        out: List[Dict] = []
        for i, r in enumerate(rows, start=1):
            out.append({
                "place": i,
                "tg_id": int(r["tg_id"]),
                "name": r["name"] or f"Гравець {r['tg_id']}",
                "medals": int(r["medals"]),
                "hp_destroyed": int(r["hp_destroyed"]),
                "kills_total": int(r["kills_total"]),
            })
        return out
    except Exception as e:
        logger.warning(f"night_watch.get_week_leaderboard failed: {e}")
        return []


async def get_player_rank(tg_id: int) -> Optional[Dict]:
    """Позиція конкретного гравця за поточний тиждень."""
    if not await ensure_schema():
        return None

    wy, wn = _current_week_key()
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                WITH ranked AS (
                  SELECT
                    nw.tg_id,
                    p.name,
                    nw.medals,
                    nw.hp_destroyed,
                    nw.kills_total,
                    RANK() OVER (
                      ORDER BY nw.medals DESC,
                               nw.hp_destroyed DESC,
                               nw.kills_total DESC,
                               nw.tg_id ASC
                    ) AS place
                  FROM night_watch_progress AS nw
                  LEFT JOIN players AS p ON p.tg_id = nw.tg_id
                  WHERE nw.week_year=$1 AND nw.week_num=$2
                )
                SELECT * FROM ranked WHERE tg_id=$3;
            """, wy, wn, tg_id)

        if not row:
            return None

        return {
            "place": int(row["place"]),
            "tg_id": int(tg_id),
            "name": row["name"] or f"Гравець {tg_id}",
            "medals": int(row["medals"]),
            "hp_destroyed": int(row["hp_destroyed"]),
            "kills_total": int(row["kills_total"]),
        }
    except Exception as e:
        logger.warning(f"night_watch.get_player_rank failed {tg_id}: {e}")
        return None


# ============================================================
# ФІНАЛІЗАЦІЯ ТИЖНЯ
# ============================================================

async def finalize_current_week(bot=None) -> List[Dict]:
    """
    Викликається на ресеті (неділя 23:59).

    Нагороди:
      1 місце → 5 клейнодів
      2 місце → 1 клейнод
      3 місце → 100 червонців
    """
    if not await ensure_schema():
        return []

    wy, wn = _current_week_key()

    rewards = {
        1: {"kleynody": 5, "coins": 0},
        2: {"kleynody": 1, "coins": 0},
        3: {"kleynody": 0, "coins": 100},
    }

    winners: List[Dict] = []

    try:
        # 1. Витягнути топ-3 за тиждень
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT p.name,
                       nw.tg_id,
                       nw.medals,
                       nw.hp_destroyed,
                       nw.kills_total
                FROM night_watch_progress AS nw
                LEFT JOIN players AS p ON p.tg_id = nw.tg_id
                WHERE nw.week_year=$1 AND nw.week_num=$2
                ORDER BY nw.medals DESC,
                         nw.hp_destroyed DESC,
                         nw.kills_total DESC,
                         nw.tg_id ASC
                LIMIT 3;
            """, wy, wn)

        # 2. Роздати нагороди, записати переможців
        for place, r in enumerate(rows, start=1):
            uid = int(r["tg_id"])
            name = r["name"] or f"Гравець {uid}"

            rw = rewards.get(place, {"kleynody": 0, "coins": 0})
            k_add = int(rw["kleynody"])
            c_add = int(rw["coins"])

            # нагорода в гаманці
            if k_add:
                await add_kleynody(uid, k_add)
            if c_add:
                await add_coins(uid, c_add)

            # запис у winners в БД
            try:
                pool2 = await get_pool()
                async with pool2.acquire() as conn2:
                    await conn2.execute("""
                        INSERT INTO night_watch_winners
                        (week_year, week_num, place, tg_id, reward_coins, reward_kleynody, created_at)
                        VALUES ($1,$2,$3,$4,$5,$6,now());
                    """, wy, wn, place, uid, c_add, k_add)
            except Exception as e:
                logger.warning(f"night_watch.insert winner failed {uid}: {e}")

            winners.append({
                "place": place,
                "tg_id": uid,
                "name": name,
                "kleynody": k_add,
                "coins": c_add,
            })

            # DM переможцю (не використовується у мініапі; лишаємо як no-op)
            if bot:
                try:
                    msg_lines = [f"🏵 Ти посів {place}-е місце у «Нічній Варті», {name}!"]
                    if k_add:
                        msg_lines.append(f"🎁 Отримано: {k_add} клейнодів")
                    if c_add:
                        msg_lines.append(f"💰 Отримано: {c_add} червонців")
                    await bot.send_message(uid, "\n".join(msg_lines))
                except Exception:
                    pass

        return winners

    except Exception as e:
        logger.warning(f"night_watch.finalize_current_week failed: {e}")
        return []