# services/night_watch_scheduler.py
from __future__ import annotations
import os, asyncio, datetime as dt
from loguru import logger
from . import night_watch as nw

RESET_DOW   = int(os.getenv("NIGHT_WATCH_RESET_DOW", "6"))      # 0=Mon … 6=Sun (UTC)
RESET_HOUR  = int(os.getenv("NIGHT_WATCH_RESET_HOUR", "23"))     # UTC
RESET_MIN   = int(os.getenv("NIGHT_WATCH_RESET_MINUTE", "59"))   # UTC
LOOP_SLEEP  = int(os.getenv("NIGHT_WATCH_LOOP_SLEEP_SEC", "60")) # період перевірки

async def _already_finalized(y: int, w: int) -> bool:
    if not await nw.ensure_schema():
        return True
    try:
        pool = await nw.get_pool()  # type: ignore[attr-defined]
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM night_watch_winners WHERE week_year=$1 AND week_num=$2 LIMIT 1",
                y, w
            )
            return bool(row)
    except Exception as e:
        logger.warning(f"night_watch: check winners failed: {e}")
        return True

async def night_watch_weekly_loop():
    logger.info("NightWatch scheduler started (UTC).")
    while True:
        try:
            now = dt.datetime.utcnow()
            if (now.weekday() == RESET_DOW and
                now.hour == RESET_HOUR and
                now.minute == RESET_MIN):

                y, w = nw._current_week_key(now)  # noqa: SLF (внутрішня утиліта)
                if not await _already_finalized(y, w):
                    winners = await nw.finalize_current_week()
                    logger.info(f"NightWatch finalized week {y}-W{w}: {winners}")
                else:
                    logger.info(f"NightWatch: week {y}-W{w} already finalized, skip.")
        except Exception as e:
            logger.error(f"NightWatch loop error: {e}")

        await asyncio.sleep(LOOP_SLEEP)