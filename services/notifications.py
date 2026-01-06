from __future__ import annotations

import os

import httpx
from loguru import logger

from db import get_pool

# URL ендпоінта aiogram-бота, який шле повідомлення в Telegram.
# Наприклад:
#   BOT_NOTIFY_URL = "https://kyrhanu-bot-production.up.railway.app/admin/notify"
BOT_NOTIFY_URL = os.getenv("BOT_NOTIFY_URL")


async def _send_to_bot(tg_id: int, text: str) -> bool:
    """
    Надсилає POST на бекенд бота, щоб той уже відправив повідомлення в Telegram.
    Повертає True, якщо відповідь 2xx, інакше False.
    """
    if not BOT_NOTIFY_URL:
        logger.error("BOT_NOTIFY_URL is not set – не можемо відправити notify")
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                BOT_NOTIFY_URL,
                json={"tg_id": tg_id, "text": text},
            )

        if 200 <= resp.status_code < 300:
            return True

        logger.error(
            "Notify request failed: status={status} body={body}",
            status=resp.status_code,
            body=resp.text[:500],
        )
        return False

    except Exception as e:
        logger.error("notify error for tg_id={tg_id}: {err!r}", tg_id=tg_id, err=e)
        return False


async def send_broadcast_to_all(text: str, limit: int | None = None) -> int:
    """
    Розсилка всім гравцям.
    limit — обмеження для тестів (наприклад, 10).
    Повертає кількість успішно відправлених запитів до бота.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tg_id
            FROM players
            ORDER BY tg_id
            LIMIT COALESCE($1, 99999999)
            """,
            limit,
        )

    logger.info(
        "[notify_all] found {count} players (limit={limit})",
        count=len(rows),
        limit=limit,
    )

    sent = 0
    for r in rows:
        tg_id = r["tg_id"]
        ok = await _send_to_bot(tg_id, text)
        if ok:
            sent += 1
        else:
            logger.warning("[notify_all] failed to notify tg_id={tg_id}", tg_id=tg_id)

    logger.info("[notify_all] successfully notified {sent} players", sent=sent)
    return sent


async def send_reengagement_to_inactive(
    text: str,
    days_inactive: int,
    limit: int | None = None,
) -> int:
    """
    Розсилка тим, хто не заходив X днів.
    Використовує поле last_activity у players.
    Повертає кількість успішно відправлених запитів до бота.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tg_id
            FROM players
            WHERE last_activity IS NOT NULL
              AND last_activity < (NOW() - ($1 || ' days')::interval)
            ORDER BY last_activity
            LIMIT COALESCE($2, 99999999)
            """,
            days_inactive,
            limit,
        )

    logger.info(
        "[notify_inactive] found {count} inactive players "
        "(days_inactive={days}, limit={limit})",
        count=len(rows),
        days=days_inactive,
        limit=limit,
    )

    sent = 0
    for r in rows:
        tg_id = r["tg_id"]
        ok = await _send_to_bot(tg_id, text)
        if ok:
            sent += 1
        else:
            logger.warning(
                "[notify_inactive] failed to notify inactive tg_id={tg_id}",
                tg_id=tg_id,
            )

    logger.info(
        "[notify_inactive] successfully notified {sent} inactive players", sent=sent
    )
    return sent