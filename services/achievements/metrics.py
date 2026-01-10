# services/achievements/metrics.py
from __future__ import annotations

from typing import Any
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
