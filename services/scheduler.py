# src/services/scheduler.py
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from loguru import logger

# DB (для legacy-чистки, якщо дуже хочеш зберегти "ratings")
try:
    from ..database import get_pool  # type: ignore
except Exception:
    get_pool = None  # type: ignore

# Перун ELO ресети
try:
    from .pvp_stats import reset_day, reset_week, reset_month  # type: ignore
except Exception:
    async def reset_day() -> bool:
        logger.warning("pvp_stats.reset_day missing")
        return False
    async def reset_week() -> bool:
        logger.warning("pvp_stats.reset_week missing")
        return False
    async def reset_month() -> bool:
        logger.warning("pvp_stats.reset_month missing")
        return False

# Жертвопринесення (щомісячна фіналізація)
try:
    from .sacrifice_event import finalize_month  # type: ignore
except Exception:
    async def finalize_month(bot=None):
        logger.warning("sacrifice_event.finalize_month missing")
        return []


# ────────────────────────────────────────────────────────────────────
# Внутрішні хелпери часу
# ────────────────────────────────────────────────────────────────────

def _seconds_until_next(hour: int, minute: int = 0, second: int = 0) -> float:
    """
    Скільки секунд до найближчого часу сьогодні/завтра (локальний час процеса).
    """
    now = datetime.now()
    next_run = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


def _seconds_until_next_weekday(target_weekday: int, hour: int, minute: int = 0, second: int = 0) -> float:
    """
    Скільки секунд до найближчого target_weekday (Mon=0 .. Sun=6) о H:M:S.
    """
    now = datetime.now()
    days_ahead = (target_weekday - now.weekday()) % 7
    next_run = (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=second, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=7)
    return (next_run - now).total_seconds()


def _seconds_until_first_of_next_month(hour: int, minute: int = 0, second: int = 0) -> float:
    """
    Скільки секунд до 1 числа наступного місяця о H:M:S.
    """
    now = datetime.now()
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    next_run = datetime(year, month, 1, hour, minute, second)
    return (next_run - now).total_seconds()


# ────────────────────────────────────────────────────────────────────
# Робочі цикли
# ────────────────────────────────────────────────────────────────────

async def _daily_perun_reset_loop():
    """
    Щодня о 03:00 — скид добового ELO.
    """
    while True:
        await asyncio.sleep(_seconds_until_next(3, 0, 0))
        try:
            ok = await reset_day()
            if ok:
                logger.info("Daily Perun ELO reset done (elo_day -> 1000).")
            else:
                logger.warning("Daily Perun ELO reset returned False.")
        except Exception as e:
            logger.exception(f"Daily Perun reset failed: {e}")


async def _weekly_perun_reset_loop():
    """
    Щонеділі о 03:00 — скид тижневого ELO.
    """
    while True:
        await asyncio.sleep(_seconds_until_next_weekday(6, 3, 0, 0))  # Sunday=6
        try:
            ok = await reset_week()
            if ok:
                logger.info("Weekly Perun ELO reset done (elo_week -> 1000).")
            else:
                logger.warning("Weekly Perun ELO reset returned False.")
        except Exception as e:
            logger.exception(f"Weekly Perun reset failed: {e}")


async def _monthly_perun_reset_and_finalize_loop(bot=None):
    """
    Щомісяця 1 числа о 03:00 —:
      - скид місячного ELO
      - фіналізація «Жертва Богам» за попередній місяць
    """
    while True:
        await asyncio.sleep(_seconds_until_first_of_next_month(3, 0, 0))
        try:
            ok = await reset_month()
            if ok:
                logger.info("Monthly Perun ELO reset done (elo_month -> 1000).")
            else:
                logger.warning("Monthly Perun ELO reset returned False.")
        except Exception as e:
            logger.exception(f"Monthly Perun reset failed: {e}")

        try:
            winners = await finalize_month(bot=bot)
            if winners:
                logger.info(f"Sacrifice: finalized previous month, winners={winners!r}")
            else:
                logger.info("Sacrifice: finalize_month returned empty.")
        except Exception as e:
            logger.exception(f"Sacrifice finalize_month failed: {e}")


# (Необов’язково) Легасі: якщо десь ще є таблиця ratings — чистимо раз на тиждень після ELO-ресету.
async def _legacy_ratings_cleanup_loop():
    if not get_pool:
        return
    while True:
        await asyncio.sleep(_seconds_until_next_weekday(6, 3, 5, 0))  # Неділя 03:05
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute("DO $$ BEGIN IF to_regclass('public.ratings') IS NOT NULL THEN DELETE FROM ratings; END IF; END $$;")
            logger.info("Legacy table 'ratings' cleaned (if existed).")
        except Exception as e:
            logger.warning(f"Legacy ratings cleanup failed: {e}")


# ────────────────────────────────────────────────────────────────────
# Публічні точки входу
# ────────────────────────────────────────────────────────────────────

async def run_all_schedulers(*, bot=None) -> None:
    """
    Запусти всі цикли планувальника.
    Використай у main: `asyncio.create_task(run_all_schedulers(bot=bot))`
    """
    asyncio.create_task(_daily_perun_reset_loop())
    asyncio.create_task(_weekly_perun_reset_loop())
    asyncio.create_task(_monthly_perun_reset_and_finalize_loop(bot=bot))
    asyncio.create_task(_legacy_ratings_cleanup_loop())
    logger.info("Schedulers started: daily/weekly/monthly + legacy cleanup.")


# Сумісність із існуючим викликом у твоєму main.py
# Раніше ти робив: asyncio.create_task(weekly_reset_loop())
# Залишаю оболонку, яка піднімає всі цикли.
async def weekly_reset_loop():
    await run_all_schedulers()