# services/progress.py
from __future__ import annotations

from typing import Optional, Tuple
from loguru import logger

# ---------- DB ----------
# ВАЖЛИВО: тут має бути той самий імпорт, що і в registration/auth
try:
    from db import get_pool  # async getter for asyncpg pool
except Exception:
    try:
        from .db import get_pool  # на випадок, якщо модуль як пакет
    except Exception:
        get_pool = None  # fallback без БД

# ---------- Контент мобів (щоб знати їх рівень) ----------
try:
    from content.mobs import MOBS  # список мобів із полями id, name, level
except Exception:
    try:
        from .content.mobs import MOBS
    except Exception:
        MOBS = []


# ───────────────────── СХЕМА ─────────────────────

async def _ensure_player_progress_schema() -> bool:
    """
    Гарантуємо, що в players є:
      level INT NOT NULL DEFAULT 1
      xp    INT NOT NULL DEFAULT 0
    """
    if not get_pool:
        logger.warning("progress: no DB pool, XP/level will be NO-OP")
        return False

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "ALTER TABLE players "
                "ADD COLUMN IF NOT EXISTS level INT DEFAULT 1;"
            )
            await conn.execute(
                "ALTER TABLE players "
                "ADD COLUMN IF NOT EXISTS xp INT DEFAULT 0;"
            )
            await conn.execute("UPDATE players SET level=COALESCE(level,1);")
            await conn.execute("UPDATE players SET xp=COALESCE(xp,0);")
        return True
    except Exception as e:
        logger.warning(f"progress: failed to ensure schema: {e}")
        return False


async def _ensure_player_row(tg_id: int) -> Tuple[int, int]:
    """
    Дає (level, xp) для гравця, гарантуючи, що рядок існує.
    Якщо рядка нема — створить з дефолтами (level=1, xp=0).
    """
    if not get_pool:
        return (1, 0)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT level, xp FROM players WHERE tg_id=$1",
            tg_id
        )
        if not row:
            await conn.execute(
                "INSERT INTO players (tg_id, level, xp) "
                "VALUES ($1, 1, 0) "
                "ON CONFLICT (tg_id) DO NOTHING",
                tg_id
            )
            level = 1
            xp = 0
        else:
            level = int(row["level"] or 1)
            xp = int(row["xp"] or 0)
    return (level, xp)


# ───────────────────── КРИВА XP ─────────────────────
# Обери профіль складності: "light" / "hard" / "brutal"
XP_CURVE = "hard"

def _need_light(L: int) -> int:
    # Плавна (для тестів)
    return int(round(40 + 10 * L + 10 * (L ** 2)))        # L10 ~1140

def _need_hard(L: int) -> int:
    # Важка — рекомендую для лайву
    return int(round(60 + 14 * L + 18 * (L ** 2)))        # L10 ~2000, L12 ~2820

def _need_brutal(L: int) -> int:
    # Дуже важка — для «хардкору»
    return int(round(100 + 12 * L + 22 * (L ** 2) + 1.1 * (L ** 3)))  # L10 ~3520, L12 ~5310

def xp_required_for(level: int) -> int:
    """
    Скільки XP треба, щоб перейти з цього level на наступний.
    """
    L = max(1, int(level))
    if XP_CURVE == "light":
        return _need_light(L)
    if XP_CURVE == "brutal":
        return _need_brutal(L)
    return _need_hard(L)


# ───────────────────── ПОШУК МОБА ─────────────────────

def _get_mob_by_code(mob_code: str):
    """
    Знаходимо моба по:
    - точному str(id)
    - int(id)
    - імені
    """
    for m in MOBS:
        mid = getattr(m, "id", None)
        if mid is not None and str(mid) == str(mob_code):
            return m

    try:
        code_int = int(str(mob_code))
        for m in MOBS:
            mid = getattr(m, "id", None)
            if mid is not None and int(mid) == code_int:
                return m
    except Exception:
        pass

    for m in MOBS:
        nm = getattr(m, "name", None)
        if nm and str(nm) == str(mob_code):
            return m

    return None


# ───────────────────── РОЗРАХУНОК XP ЗА МОБА ─────────────────────

def calc_xp_reward(mob_code: str, player_level: Optional[int] = None) -> int:
    """
    Скільки XP дає моб.
    Формула (під важку криву, щоб прогрес був повільнішим):
      base = 6 + 2 * mob_lvl
      delta = clamp(mob_lvl - player_level, -6, +8)
      reward = base + delta
      мінімум 4
    """
    m = _get_mob_by_code(mob_code)
    if not m:
        return 10  # запасний дефолт

    mob_lvl = int(getattr(m, "level", 1))
    base = 6 + 2 * mob_lvl

    if isinstance(player_level, int) and player_level > 0:
        delta = mob_lvl - player_level
        if delta < -6:
            delta = -6
        elif delta > 8:
            delta = 8
        base += delta

    return max(4, base)


# ───────────────────── ВНУТРІШНЄ ПІДВИЩЕННЯ РІВНЯ ─────────────────────

def _apply_xp_and_level_up(
    level: int,
    xp_cur: int,
    xp_gain: int,
) -> Tuple[int, int, int]:
    """
    Додаємо xp_gain до xp_cur, і робимо level-up скільки треба разів.
    Залишок XP НЕ губиться.
    """
    xp_cur += max(0, int(xp_gain))
    while True:
        need = xp_required_for(level)
        if xp_cur >= need:
            xp_cur -= need
            level += 1
        else:
            break
    next_need = xp_required_for(level)
    return (level, xp_cur, next_need)


# ───────────────────── ПУБЛІЧНІ ФУНКЦІЇ ─────────────────────

async def add_player_xp(tg_id: int, amount: int) -> Tuple[int, int, int, int]:
    """
    Докинути «сирий» XP гравцю (без моба).
    Повертає (xp_gain, new_level, new_xp, xp_needed_for_next).
    """
    if amount <= 0:
        return (0, 0, 0, 0)
    if not await _ensure_player_progress_schema():
        return (0, 0, 0, 0)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT level, xp FROM players WHERE tg_id=$1",
                tg_id
            )
            if not row:
                await conn.execute(
                    "INSERT INTO players (tg_id, level, xp) VALUES ($1, 1, 0) "
                    "ON CONFLICT (tg_id) DO NOTHING",
                    tg_id
                )
                level = 1
                xp = 0
            else:
                level = int(row["level"] or 1)
                xp = int(row["xp"] or 0)

            xp += amount
            while True:
                need = xp_required_for(level)
                if xp >= need:
                    xp -= need
                    level += 1
                else:
                    break

            await conn.execute(
                "UPDATE players SET level=$2, xp=$3 WHERE tg_id=$1",
                tg_id, level, xp
            )
            next_need = xp_required_for(level)
            return (amount, level, xp, next_need)
    except Exception as e:
        logger.warning(f"add_player_xp failed tg_id={tg_id}: {e}")
        return (0, 0, 0, 0)


async def grant_xp_for_win(tg_id: int, mob_code: str) -> Tuple[int, int, int, int]:
    """
    Викликається з бою.
    Рахує XP за моба і застосовує так само, як add_player_xp.
    Повертає (xp_gain, new_level, new_xp, xp_needed_next).
    """
    if not await _ensure_player_progress_schema():
        return (0, 0, 0, 0)

    if not get_pool:
        logger.warning("grant_xp_for_win: no DB pool")
        return (0, 0, 0, 0)

    try:
        # поточний рівень/XP
        level, xp_cur = await _ensure_player_row(tg_id)
        # скільки дати за цього моба
        gain = calc_xp_reward(mob_code, player_level=level)
        # апдейт локально
        new_level, new_xp, next_need = _apply_xp_and_level_up(
            level, xp_cur, gain
        )

        # пишемо назад у players
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE players SET level=$2, xp=$3 WHERE tg_id=$1",
                tg_id,
                new_level,
                new_xp,
            )

        logger.info(
            f"XP gain: uid={tg_id} +{gain} → lvl {new_level}, xp {new_xp}/{next_need}"
        )

        return (gain, new_level, new_xp, next_need)

    except Exception as e:
        logger.warning(f"grant_xp_for_win failed tg_id={tg_id}: {e}")
        return (0, 0, 0, 0)