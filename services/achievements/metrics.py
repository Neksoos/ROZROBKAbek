# services/achievements/metrics.py
from __future__ import annotations

from typing import Optional
from db import get_pool


async def inc_metric(tg_id: int, key: str, delta: int = 1) -> None:
    if tg_id <= 0 or not key:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO player_metrics(tg_id, key, value)
            VALUES($1,$2,$3)
            ON CONFLICT (tg_id, key)
            DO UPDATE SET value = player_metrics.value + EXCLUDED.value,
                          updated_at = now()
            """,
            tg_id,
            key,
            int(delta),
        )


async def set_metric_max(tg_id: int, key: str, value: int) -> None:
    if tg_id <= 0 or not key:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO player_metrics(tg_id, key, value)
            VALUES($1,$2,$3)
            ON CONFLICT (tg_id, key)
            DO UPDATE SET value = GREATEST(player_metrics.value, EXCLUDED.value),
                          updated_at = now()
            """,
            tg_id,
            key,
            int(value),
        )


async def get_metric(tg_id: int, key: str) -> int:
    if tg_id <= 0 or not key:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        v = await conn.fetchval(
            "SELECT COALESCE(value, 0)::bigint FROM player_metrics WHERE tg_id=$1 AND key=$2",
            tg_id,
            key,
        )
        return int(v or 0)


async def try_mark_event_once(tg_id: int, event_key: str) -> bool:
    """
    Ідемпотентність: повертає True тільки якщо подія записалась ВПЕРШЕ.
    Вимагає таблицю player_events(tg_id, event_key).
    """
    if tg_id <= 0 or not event_key:
        return False

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO player_events(tg_id, event_key)
            VALUES($1,$2)
            ON CONFLICT (tg_id, event_key)
            DO NOTHING
            RETURNING tg_id
            """,
            tg_id,
            event_key,
        )
        return bool(row)