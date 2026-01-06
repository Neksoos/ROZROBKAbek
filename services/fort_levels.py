# services/fort_levels.py
from __future__ import annotations

from typing import Optional, Tuple
from math import ceil, sqrt
from datetime import date
from loguru import logger

# ───────────────────────── DB (мініап) ─────────────────────────
try:
    from db import get_pool  # type: ignore
except Exception:
    get_pool = None  # без БД усе no-op

# ───────────────────────── MOBS з нового світу ─────────────────────────
try:
    from data.world_data import MOBS as WORLD_MOBS  # type: ignore
except Exception:
    WORLD_MOBS = []  # type: ignore

# ───────────────────────── Константи прогресу ─────────────────────────
GUILD_MAX_LEVEL = 50

# ФІКСОВАНА вимога XP на кожен перехід рівня → наступний рівень.
FLAT_LEVEL_XP = 25_000  # головний тюнінг

# Добовий софт-кеп: після перевищення — ефективність 20%
POST_CAP_EFFICIENCY = 0.20

# Масштаб добового кепу
CAP_BASE_PER_PLAYER = 2_000
CAP_PER_LEVEL       = 300
CAP_GLOBAL_FACTOR   = 50


# ───────────────────────── Бонуси ─────────────────────────
def bonuses_for_level(level: int) -> dict:
    """
    Повертає МНОЖНИКИ як частки (0.15 = +15%), узгоджено з char_stats.
    Лінійне зростання з лімітами.
    """
    level = max(1, int(level))
    hp_pct   = min(0.25, 0.01  * level)  # +1% per level, cap 25%
    atk_pct  = min(0.15, 0.005 * level)  # +0.5% per level, cap 15%
    coin_pct = min(0.12, 0.004 * level)  # +0.4% per level, cap 12%
    drop_pct = min(0.10, 0.003 * level)  # +0.3% per level, cap 10%
    return {"hp_pct": hp_pct, "atk_pct": atk_pct, "coin_pct": coin_pct, "drop_pct": drop_pct}


def bonuses_summary(level: int) -> str:
    b = bonuses_for_level(level)
    return (
        f"Бонуси: HP +{b['hp_pct']*100:.0f}%, ATK +{b['atk_pct']*100:.1f}%, "
        f"Дохід +{b['coin_pct']*100:.1f}%, Дроп +{b['drop_pct']*100:.1f}%"
    )


# ───────────────────────── Схема ─────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fort_progress (
    fort_id BIGINT PRIMARY KEY REFERENCES forts(id) ON DELETE CASCADE,
    level   INT    NOT NULL DEFAULT 1,
    xp      BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);
-- трекаємо щоденний дохід XP для софт-кепу
CREATE TABLE IF NOT EXISTS fort_xp_daily (
    fort_id BIGINT NOT NULL REFERENCES forts(id) ON DELETE CASCADE,
    day DATE NOT NULL,
    earned BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (fort_id, day)
);
CREATE INDEX IF NOT EXISTS fort_xp_daily_idx ON fort_xp_daily(day);
"""


async def ensure_schema() -> bool:
    if not get_pool:
        logger.warning("fort_levels: no DB pool")
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            for stmt in SCHEMA_SQL.split(";"):
                s = stmt.strip()
                if s:
                    await conn.execute(s + ";")
        return True
    except Exception as e:
        logger.error(f"fort_levels.ensure_schema failed: {e}")
        return False


# ───────────────────────── Helpers ─────────────────────────
def xp_required_for(level: int) -> int:
    level = max(1, min(GUILD_MAX_LEVEL, int(level)))
    return int(ceil(FLAT_LEVEL_XP))


def _cap_fort(level: int, active: int) -> int:
    per_player = CAP_BASE_PER_PLAYER + CAP_PER_LEVEL * max(1, level)
    return round(CAP_GLOBAL_FACTOR * per_player * sqrt(max(1, active)))


async def _get_member_fort(tg_id: int) -> Optional[int]:
    if not get_pool:
        return None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT fort_id FROM fort_members WHERE tg_id=$1", tg_id)
            return int(row["fort_id"]) if row and row["fort_id"] is not None else None
    except Exception:
        return None


async def _estimate_active(fort_id: int) -> int:
    if not get_pool:
        return 10
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            n = await conn.fetchval("SELECT COUNT(*) FROM fort_members WHERE fort_id=$1", fort_id)
            return int(n or 10) or 10
    except Exception:
        return 10


async def _get_row(fort_id: int) -> Tuple[int, int]:
    if not await ensure_schema():
        return (1, 0)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT level, xp FROM fort_progress WHERE fort_id=$1", fort_id)
            if not row:
                await conn.execute(
                    "INSERT INTO fort_progress(fort_id, level, xp) VALUES ($1, 1, 0) ON CONFLICT DO NOTHING",
                    fort_id,
                )
                return (1, 0)
            return (int(row["level"]), int(row["xp"]))
    except Exception as e:
        logger.warning(f"fort_levels._get_row failed: {e}")
        return (1, 0)


async def _recent_battle_mob_level(tg_id: int, mob_code: str) -> Optional[int]:
    if not get_pool:
        return None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT mob_level
                FROM battles
                WHERE hero_id=$1 AND mob_code=$2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                tg_id,
                str(mob_code),
            )
            if row and row["mob_level"] is not None:
                return int(row["mob_level"])
    except Exception as e:
        logger.warning(f"_recent_battle_mob_level failed: {e}")
    return None


# ───────────────────────── Публічний API ─────────────────────────
async def get_fort_level(fort_id: int) -> Tuple[int, int, int]:
    level, xp = await _get_row(fort_id)
    if level >= GUILD_MAX_LEVEL:
        return (level, xp, 0)
    need = xp_required_for(level)
    return (level, xp, need)


async def add_fort_xp(fort_id: int, gain: int) -> Tuple[int, int, int, int]:
    """
    Повертає: (applied_gain, level, xp_in_current_level, need_for_next_level)
    """
    gain = max(0, int(gain))
    if gain == 0 or not await ensure_schema():
        lvl, xp, need = await get_fort_level(fort_id)
        return (0, lvl, xp, need)

    today = date.today()
    active = await _estimate_active(fort_id)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            lvl, xp = await _get_row(fort_id)
            if lvl >= GUILD_MAX_LEVEL:
                return (0, lvl, xp, 0)

            drow = await conn.fetchrow(
                "SELECT earned FROM fort_xp_daily WHERE fort_id=$1 AND day=$2",
                fort_id,
                today,
            )
            earned_today = int(drow["earned"]) if drow else 0
            cap = _cap_fort(lvl, active)

            eff = 1.0 if earned_today < cap else POST_CAP_EFFICIENCY
            applied = int(gain * eff)
            if applied <= 0:
                need_after = 0 if lvl >= GUILD_MAX_LEVEL else xp_required_for(lvl)
                logger.info(
                    "fort_xp: applied=0 (gain={} eff={} earned_today={} cap={})",
                    gain,
                    eff,
                    earned_today,
                    cap,
                )
                return (0, lvl, xp, need_after)

            xp += applied

            while lvl < GUILD_MAX_LEVEL:
                need = xp_required_for(lvl)
                if xp < need:
                    break
                xp -= need
                lvl += 1

            await conn.execute(
                "INSERT INTO fort_xp_daily(fort_id, day, earned) VALUES ($1,$2,$3) "
                "ON CONFLICT (fort_id, day) DO UPDATE SET earned = fort_xp_daily.earned + EXCLUDED.earned",
                fort_id,
                today,
                applied,
            )
            await conn.execute(
                "INSERT INTO fort_progress(fort_id, level, xp) VALUES ($1,$2,$3) "
                "ON CONFLICT (fort_id) DO UPDATE SET level=EXCLUDED.level, xp=EXCLUDED.xp, updated_at=now()",
                fort_id,
                lvl,
                xp,
            )

            need_after = 0 if lvl >= GUILD_MAX_LEVEL else xp_required_for(lvl)
            return (applied, lvl, xp, need_after)
    except Exception as e:
        logger.warning(f"fort_levels.add_fort_xp failed: {e}")
        lvl, xp, need = await get_fort_level(fort_id)
        return (0, lvl, xp, need)


# ───────────────────────── Гачки подій ─────────────────────────
async def add_fort_xp_for_kill(
    tg_id: int,
    mob_code: str,
    player_level: Optional[int] = None,
) -> Tuple[int, Optional[int], int]:
    """
    Нарахувати XP заставі за вбивство моба учасником.
    Формула: 5 + 2*min(10, mob_level) [+1 якщо моб вищого рівня за гравця].
    """
    if not get_pool:
        return (0, None, 0)

    # 1) шукаємо рівень моба у data.world_data.MOBS
    mob_lvl = 1
    try:
        code_str = str(mob_code)
        mob_id: Optional[int] = None

        if code_str.startswith("mob_"):
            tail = code_str.split("_")[-1]
            if tail.isdigit():
                mob_id = int(tail)
        elif code_str.isdigit():
            mob_id = int(code_str)

        if mob_id is not None:
            for area_key, mob_list in WORLD_MOBS:  # type: ignore
                for mid, _name, level in mob_list:
                    if int(mid) == mob_id:
                        mob_lvl = int(level)
                        break
                if mob_lvl > 1:
                    break
    except Exception as e:
        logger.warning(f"add_fort_xp_for_kill: mobs lookup failed via WORLD_MOBS: {e}")

    # 2) fallback – дивимось останній запис у battles
    if mob_lvl <= 1:
        lvl_from_battle = await _recent_battle_mob_level(tg_id, mob_code)
        if isinstance(lvl_from_battle, int) and lvl_from_battle > 0:
            mob_lvl = lvl_from_battle

    gain = 5 + 2 * min(10, mob_lvl)
    if isinstance(player_level, int) and player_level > 0 and mob_lvl > player_level:
        gain += 1

    fort_id = await _get_member_fort(tg_id)
    if not fort_id:
        logger.info("add_fort_xp_for_kill: no fort for tg_id={}", tg_id)
        return (0, None, 0)

    g_gain, new_level, total_xp, _need = await _add_and_report(fort_id, gain)
    logger.info(
        "add_fort_xp_for_kill: tg_id={} fort_id={} mob_lvl={} gain={} applied={} new_level={}",
        tg_id,
        fort_id,
        mob_lvl,
        gain,
        g_gain,
        new_level,
    )
    return (g_gain, new_level, total_xp)


async def add_fort_xp_for_quest(
    tg_id: int,
    quest_code: str,
    difficulty: int = 1,
) -> Tuple[int, Optional[int], int]:
    if not get_pool:
        return (0, None, 0)
    difficulty = max(1, min(5, int(difficulty)))
    gain = 12 * difficulty

    fort_id = await _get_member_fort(tg_id)
    if not fort_id:
        return (0, None, 0)

    g_gain, new_level, total_xp, _need = await _add_and_report(fort_id, gain)
    return (g_gain, new_level, total_xp)


# внутрішній помічник
async def _add_and_report(fort_id: int, gain: int) -> Tuple[int, Optional[int], int, int]:
    prev_level, _xp, _need = await get_fort_level(fort_id)
    g_gain, lvl, total_xp, need_after = await add_fort_xp(fort_id, gain)
    level_up = lvl if lvl > prev_level else None
    return (g_gain, level_up, total_xp, need_after)